"""Generate FastAPI routes from registered resources.

FastAPI only adapts HTTP to ``ResourceManager`` calls: no business logic lives here.
Endpoints are built with explicit signatures so that FastAPI validates bodies with
the resource's Pydantic schemas and documents them in OpenAPI.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Path, Query, Response, status
from pydantic import BaseModel, create_model
from resource_engine import ListQuery, ResourceDefinition, ResourceManager, ResourceRegistry

from resource_engine_fastapi.documents import (
    add_owner_document_routes,
    build_document_content_router,
)
from resource_engine_fastapi.errors import install_exception_handlers

type ActorDependency = Callable[..., Any]

_KW = inspect.Parameter.KEYWORD_ONLY


def _no_actor() -> None:
    return None


def _param(name: str, annotation: Any, default: Any) -> inspect.Parameter:
    return inspect.Parameter(name, _KW, annotation=annotation, default=default)


def _endpoint(
    fn: Callable[..., Any], params: list[inspect.Parameter], name: str
) -> Callable[..., Any]:
    fn.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    fn.__name__ = name
    return fn


def _summary(text: str | None) -> str | None:
    return text.strip().splitlines()[0] if text and text.strip() else None


def _page_model(read: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{read.__name__}Page",
        items=(list[read], ...),  # type: ignore[valid-type]
        total=(int, ...),
        offset=(int, ...),
        limit=(int, ...),
    )


def build_resource_router(
    definition: ResourceDefinition,
    manager: ResourceManager,
    *,
    actor_dependency: ActorDependency = _no_actor,
) -> APIRouter:
    """Routes for one resource: CRUD, ``/transitions/<event>`` and ``/actions/<name>``.

    ``actor_dependency`` is a FastAPI dependency whose result is passed to the
    manager as ``actor``. It is the integration point for authentication; the
    engine does not interpret it yet.
    """
    name = definition.name
    router = APIRouter(prefix=f"/{name}", tags=[name])
    id_param = _param("entity_id", definition.id_type, Path(...))
    actor_param = _param("actor", Any, Depends(actor_dependency))
    read = definition.schemas.get("read")
    docs = definition.operation_docs

    if definition.enabled("list"):
        filter_params = []
        for fld in definition.filterable:
            annotation = read.model_fields[fld].annotation if read else str
            filter_params.append(_param(fld, annotation | None, Query(None)))  # type: ignore[operator]

        async def list_endpoint(**kw: Any) -> Any:
            order = kw.pop("order_by")
            query = ListQuery(
                offset=kw.pop("offset"),
                limit=kw.pop("limit"),
                order_by=[o for o in (order or "").split(",") if o],
                filters={k: v for k, v in kw.items() if k != "actor" and v is not None},
            )
            page = await manager.list(name, query, actor=kw.get("actor"))
            return {
                "items": page.items,
                "total": page.total,
                "offset": page.offset,
                "limit": page.limit,
            }

        router.add_api_route(
            "",
            _endpoint(
                list_endpoint,
                [
                    _param("offset", int, Query(0, ge=0)),
                    _param("limit", int, Query(50, ge=1, le=definition.max_page_size)),
                    _param(
                        "order_by",
                        str | None,
                        Query(
                            None, description="Comma-separated fields; prefix '-' for descending"
                        ),
                    ),
                    *filter_params,
                    actor_param,
                ],
                f"list_{name}",
            ),
            methods=["GET"],
            response_model=_page_model(read) if read else None,
            summary=_summary(docs.get("list")) or f"List {name}",
            description=docs.get("list"),
        )

    if definition.enabled("get"):

        async def get_endpoint(entity_id: Any, actor: Any) -> Any:
            return await manager.get(name, entity_id, actor=actor)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(get_endpoint, [id_param, actor_param], f"get_{name}"),
            methods=["GET"],
            response_model=read,
            summary=_summary(docs.get("get")) or f"Get one of {name}",
            description=docs.get("get"),
        )

    if definition.enabled("create"):

        async def create_endpoint(payload: Any, actor: Any) -> Any:
            return await manager.create(name, payload, actor=actor)

        router.add_api_route(
            "",
            _endpoint(
                create_endpoint,
                [_param("payload", definition.schemas["create"], Body(...)), actor_param],
                f"create_{name}",
            ),
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_model=read,
            summary=_summary(docs.get("create")) or f"Create {name}",
            description=docs.get("create"),
        )

    if definition.enabled("update"):

        async def update_endpoint(entity_id: Any, payload: BaseModel, actor: Any) -> Any:
            return await manager.update(name, entity_id, payload, actor=actor)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(
                update_endpoint,
                [id_param, _param("payload", definition.schemas["update"], Body(...)), actor_param],
                f"update_{name}",
            ),
            methods=["PATCH"],
            response_model=read,
            summary=_summary(docs.get("update")) or f"Update {name}",
            description=docs.get("update"),
        )

    if definition.enabled("delete"):

        async def delete_endpoint(entity_id: Any, actor: Any) -> Response:
            await manager.delete(name, entity_id, actor=actor)
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(delete_endpoint, [id_param, actor_param], f"delete_{name}"),
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            summary=_summary(docs.get("delete")) or f"Delete {name}",
            description=docs.get("delete"),
        )

    if definition.machine is not None and definition.expose_transitions:
        sm_doc = inspect.getdoc(definition.state_machine) or ""

        async def allowed_endpoint(entity_id: Any, actor: Any) -> list[str]:
            return await manager.allowed_transitions(name, entity_id, actor=actor)

        router.add_api_route(
            "/{entity_id}/transitions",
            _endpoint(allowed_endpoint, [id_param, actor_param], f"allowed_transitions_{name}"),
            methods=["GET"],
            response_model=list[str],
            summary="Transitions allowed from the current state",
        )
        for event in definition.machine.info.events:
            router.add_api_route(
                f"/{{entity_id}}/transitions/{event}",
                _transition_endpoint(definition, manager, event, id_param, actor_param),
                methods=["POST"],
                response_model=read,
                summary=f"Transition: {event}",
                description=sm_doc or None,
            )

    for action in definition.actions.values():

        def make_action(
            action_name: str = action.name,
            schema: Any = action.input_schema,
            instance: bool = action.instance,
        ) -> Callable[..., Any]:
            async def action_endpoint(**kw: Any) -> Any:
                return await manager.run_action(
                    name, action_name, kw.get("entity_id"), kw.get("payload"), actor=kw["actor"]
                )

            params = [id_param] if instance else []
            if schema is not None:
                params.append(_param("payload", schema, Body(...)))
            params.append(actor_param)
            return _endpoint(action_endpoint, params, f"{name}_{action_name}")

        path = (
            f"/{{entity_id}}/actions/{action.name}"
            if action.instance
            else f"/actions/{action.name}"
        )
        response_model = action.output_schema or (read if action.instance else None)
        router.add_api_route(
            path,
            make_action(),
            methods=["POST"],
            response_model=response_model,
            summary=_summary(action.description) or f"Action: {action.name}",
            description=action.description,
        )

    add_owner_document_routes(router, definition, manager, actor_dependency)
    return router


def _transition_endpoint(
    definition: ResourceDefinition,
    manager: ResourceManager,
    event: str,
    id_param: inspect.Parameter,
    actor_param: inspect.Parameter,
) -> Callable[..., Any]:
    schema = definition.transition_schemas.get(event)

    async def transition_endpoint(**kw: Any) -> Any:
        return await manager.transition(
            definition.name, kw["entity_id"], event, kw.get("payload"), actor=kw["actor"]
        )

    params = [id_param]
    if schema is not None:
        params.append(_param("payload", schema, Body(...)))
    params.append(actor_param)
    return _endpoint(transition_endpoint, params, f"{definition.name}_{event}")


def build_router(
    registry: ResourceRegistry,
    manager: ResourceManager,
    *,
    prefix: str = "",
    actor_dependency: ActorDependency = _no_actor,
    redirect_downloads: bool = False,
) -> APIRouter:
    """Routes for every registered resource (and document downloads, if enabled).

    ``redirect_downloads`` answers downloads with a 307 to a presigned URL instead
    of streaming the content through the API.
    """
    router = APIRouter(prefix=prefix)
    if getattr(manager.authorizer, "catalog", None) is not None:
        from resource_engine_fastapi.authz import build_authz_router

        router.include_router(build_authz_router(manager, actor_dependency=actor_dependency))
    if manager.documents is not None:
        router.include_router(
            build_document_content_router(
                manager, actor_dependency=actor_dependency, redirect_downloads=redirect_downloads
            )
        )
    for definition in registry:
        router.include_router(
            build_resource_router(definition, manager, actor_dependency=actor_dependency)
        )
    return router


def mount_resources(
    app: FastAPI,
    registry: ResourceRegistry,
    manager: ResourceManager,
    *,
    prefix: str = "",
    actor_dependency: ActorDependency = _no_actor,
    redirect_downloads: bool = False,
) -> None:
    """Include every registered resource in ``app`` and install error handlers."""
    app.include_router(
        build_router(
            registry,
            manager,
            prefix=prefix,
            actor_dependency=actor_dependency,
            redirect_downloads=redirect_downloads,
        )
    )
    install_exception_handlers(app)
