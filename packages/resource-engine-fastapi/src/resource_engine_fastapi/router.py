"""Generate FastAPI routes from registered resources.

FastAPI only adapts HTTP to ``ResourceManager`` calls: no business logic lives here.
Endpoints are built with explicit signatures so that FastAPI validates bodies with
the resource's Pydantic schemas and documents them in OpenAPI.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, Path, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, create_model
from resource_engine import ListQuery, ResourceDefinition, ResourceManager, ResourceRegistry
from resource_engine.application.manager import EXPAND_PAGE_SIZE
from resource_engine.application.relations import dump_read, needs_dump
from resource_engine.errors import InvalidQuery

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


def _dump(value: Any) -> Any:
    return dump_read(value)


def _respond(
    value: Any,
    status_code: int = 200,
    *,
    version_field: str | None = None,
    response: Response | None = None,
) -> Any:
    """Results with hidden fields (field-level permissions) or expanded related
    records are serialized directly, bypassing response-model validation, which
    would require the hidden fields and drop the expansions. Versioned
    resources answer with ``ETag: "<version>"``."""
    version = getattr(value, version_field, None) if version_field else None
    etag = None if version is None else f'"{version}"'
    if needs_dump(value):
        reply = JSONResponse(_dump(value), status_code=status_code)
        if etag:
            reply.headers["ETag"] = etag
        return reply
    if etag and response is not None:
        response.headers["ETag"] = etag
    return value


def parse_if_match(value: str | None) -> int | None:
    """``If-Match: "3"`` (or ``W/"3"``) → 3; ``*`` or no header → ``None``."""
    if value is None or value.strip() == "*":
        return None
    token = value.split(",")[0].strip().removeprefix("W/").strip('"')
    try:
        return int(token)
    except ValueError:
        raise InvalidQuery(
            f"If-Match {value!r} is not a version ETag",
            hint='Send the ETag the record was read with, e.g. If-Match: "3".',
        ) from None


def _respond_page(page: dict[str, Any]) -> Any:
    items = page["items"]
    if not any(needs_dump(i) for i in items):
        return page
    return JSONResponse({**page, "items": [_dump(i) for i in items]})


def _page_model(read: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"{read.__name__}Page",
        items=(list[read], ...),  # type: ignore[valid-type]
        total=(int, ...),
        offset=(int, ...),
        limit=(int, ...),
    )


def registry_of(manager: ResourceManager) -> ResourceRegistry:
    return manager.registry


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
    # tenant-scoped resources: cross-tenant callers may name a tenant
    tenant_params = (
        [
            _param(
                "tenant",
                str | None,
                Query(None, description="Tenant to act on (cross-tenant callers)"),
            )
        ]
        if definition.tenant_field
        else []
    )
    read = definition.schemas.get("read")
    docs = definition.operation_docs
    version_field = definition.version_field
    # versioned resources: responses carry ETag, writes accept If-Match
    response_param = _param("response", Response, inspect.Parameter.empty)
    match_params = (
        [
            _param(
                "if_match",
                str | None,
                Header(
                    None,
                    alias="If-Match",
                    description="ETag of the record as read; a stale one is refused (409)",
                ),
            )
        ]
        if version_field
        else []
    )

    # every write accepts Idempotency-Key: a retry returns the first result
    key_params = [
        _param(
            "idempotency_key",
            str | None,
            Header(
                None,
                alias="Idempotency-Key",
                description="Retry-safe key: the same request with the same key runs once "
                "and later calls return its result; another request with it is refused (422)",
            ),
        )
    ]

    reverse = manager.reverse.get(name, {})
    relations = [*definition.references, *(f"{r}[:size]" for r in reverse)]
    nested_hint = ". Nest with dots (invoices.customer_record), up to 3 levels"
    expand_params = (
        [
            _param(
                "expand",
                str | None,
                Query(
                    None,
                    description="Comma-separated relations to include: "
                    + ", ".join(relations)
                    + (
                        f" (reverse relations embed one page, {EXPAND_PAGE_SIZE} by default)"
                        if reverse
                        else ""
                    )
                    + nested_hint,
                ),
            )
        ]
        if relations
        else []
    )

    def expand_of(kw: dict[str, Any]) -> tuple[str, ...]:
        raw = kw.get("expand") or ""
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    def reply(value: Any, kw: dict[str, Any], status_code: int = 200) -> Any:
        return _respond(
            value, status_code, version_field=version_field, response=kw.get("response")
        )

    if definition.enabled("list"):
        filter_params = []
        for fld in definition.filterable:
            annotation = read.model_fields[fld].annotation if read else str
            # internal name, public alias: a field may be called like a parameter (actor)
            filter_params.append(
                _param(f"filter__{fld}", annotation | None, Query(None, alias=fld))  # type: ignore[operator]
            )
        for ref in definition.references.values():
            target = registry_of(manager).get(ref.target)
            target_read = target.schemas.get("read")
            for fld in target.filterable:
                annotation = target_read.model_fields[fld].annotation if target_read else str
                filter_params.append(
                    _param(
                        f"related__{ref.name}__{fld}",
                        annotation | None,  # type: ignore[operator]
                        Query(
                            None,
                            alias=f"{ref.name}.{fld}",
                            description=f"Filter by the related {ref.target} record's {fld}",
                        ),
                    )
                )

        async def list_endpoint(**kw: Any) -> Any:
            order = kw.pop("order_by")
            filters = {
                k.removeprefix("filter__"): v
                for k, v in kw.items()
                if k.startswith("filter__") and v is not None
            }
            for k, v in kw.items():
                if k.startswith("related__") and v is not None:
                    relation, _, fld = k.removeprefix("related__").partition("__")
                    filters[f"{relation}.{fld}"] = v
            query = ListQuery(
                offset=kw.pop("offset"),
                limit=kw.pop("limit"),
                order_by=[o for o in (order or "").split(",") if o],
                filters=filters,
            )
            page = await manager.list(
                name,
                query,
                actor=kw.get("actor"),
                tenant=kw.get("tenant"),
                expand=expand_of(kw),
            )
            return _respond_page(
                {
                    "items": page.items,
                    "total": page.total,
                    "offset": page.offset,
                    "limit": page.limit,
                }
            )

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
                    *expand_params,
                    *tenant_params,
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

        async def get_endpoint(**kw: Any) -> Any:
            result = await manager.get(
                name,
                kw["entity_id"],
                actor=kw["actor"],
                tenant=kw.get("tenant"),
                expand=expand_of(kw),
            )
            return reply(result, kw)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(
                get_endpoint,
                [id_param, *expand_params, *tenant_params, actor_param, response_param],
                f"get_{name}",
            ),
            methods=["GET"],
            response_model=read,
            summary=_summary(docs.get("get")) or f"Get one of {name}",
            description=docs.get("get"),
        )

    if definition.enabled("create"):

        async def create_endpoint(**kw: Any) -> Any:
            result = await manager.create(
                name,
                kw["payload"],
                actor=kw["actor"],
                tenant=kw.get("tenant"),
                idempotency_key=kw.get("idempotency_key"),
            )
            return reply(result, kw, 201)

        router.add_api_route(
            "",
            _endpoint(
                create_endpoint,
                [
                    _param("payload", definition.schemas["create"], Body(...)),
                    *tenant_params,
                    *key_params,
                    actor_param,
                    response_param,
                ],
                f"create_{name}",
            ),
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_model=read,
            summary=_summary(docs.get("create")) or f"Create {name}",
            description=docs.get("create"),
        )

    if definition.enabled("update"):

        async def update_endpoint(**kw: Any) -> Any:
            result = await manager.update(
                name,
                kw["entity_id"],
                kw["payload"],
                actor=kw["actor"],
                tenant=kw.get("tenant"),
                expected_version=parse_if_match(kw.get("if_match")),
                idempotency_key=kw.get("idempotency_key"),
            )
            return reply(result, kw)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(
                update_endpoint,
                [
                    id_param,
                    _param("payload", definition.schemas["update"], Body(...)),
                    *tenant_params,
                    *match_params,
                    *key_params,
                    actor_param,
                    response_param,
                ],
                f"update_{name}",
            ),
            methods=["PATCH"],
            response_model=read,
            summary=_summary(docs.get("update")) or f"Update {name}",
            description=docs.get("update"),
        )

    if definition.enabled("delete"):

        async def delete_endpoint(**kw: Any) -> Response:
            await manager.delete(
                name,
                kw["entity_id"],
                actor=kw["actor"],
                tenant=kw.get("tenant"),
                expected_version=parse_if_match(kw.get("if_match")),
                idempotency_key=kw.get("idempotency_key"),
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        router.add_api_route(
            "/{entity_id}",
            _endpoint(
                delete_endpoint,
                [id_param, *tenant_params, *match_params, *key_params, actor_param],
                f"delete_{name}",
            ),
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            summary=_summary(docs.get("delete")) or f"Delete {name}",
            description=docs.get("delete"),
        )

    if definition.machine is not None and definition.expose_transitions:
        sm_doc = inspect.getdoc(definition.state_machine) or ""

        async def allowed_endpoint(entity_id: Any, actor: Any, tenant: Any = None) -> list[str]:
            return await manager.allowed_transitions(name, entity_id, actor=actor, tenant=tenant)

        router.add_api_route(
            "/{entity_id}/transitions",
            _endpoint(
                allowed_endpoint,
                [id_param, *tenant_params, actor_param],
                f"allowed_transitions_{name}",
            ),
            methods=["GET"],
            response_model=list[str],
            summary="Transitions allowed from the current state",
        )
        if manager.has_history and definition.resource_cls.audit:
            from resource_engine.audit.history import StateChange

            async def history_endpoint(entity_id: Any, actor: Any, tenant: Any = None) -> Any:
                return await manager.history(name, entity_id, actor=actor, tenant=tenant)

            router.add_api_route(
                "/{entity_id}/history",
                _endpoint(
                    history_endpoint,
                    [id_param, *tenant_params, actor_param],
                    f"history_{name}",
                ),
                methods=["GET"],
                response_model=list[StateChange],
                summary="State history",
                description="The record's creation and committed transitions, oldest "
                "first, with who made them and when (from the audit trail).",
            )
        for event in definition.machine.info.events:
            router.add_api_route(
                f"/{{entity_id}}/transitions/{event}",
                _transition_endpoint(
                    definition,
                    manager,
                    event,
                    id_param,
                    [*tenant_params, *match_params, *key_params, actor_param, response_param],
                ),
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
                result = await manager.run_action(
                    name,
                    action_name,
                    kw.get("entity_id"),
                    kw.get("payload"),
                    actor=kw["actor"],
                    tenant=kw.get("tenant"),
                    expected_version=parse_if_match(kw.get("if_match")),
                    idempotency_key=kw.get("idempotency_key"),
                )
                return reply(result, kw)

            params = [id_param] if instance else []
            if schema is not None:
                params.append(_param("payload", schema, Body(...)))
            params += [
                *tenant_params,
                *(match_params if instance else []),
                *key_params,
                actor_param,
            ]
            params.append(response_param)
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

    for relation, ref in reverse.items():
        router.add_api_route(
            f"/{{entity_id}}/{relation}",
            _related_endpoint(manager, definition, relation, ref, id_param, actor_param),
            methods=["GET"],
            response_model=_page_model(owner_read)
            if (owner_read := manager.registry.get(ref.owner).schemas.get("read"))
            else None,
            summary=f"{ref.owner} of one {name} record",
            description=(
                f"The {ref.owner} records whose {ref.field} is this {name} record"
                if ref.field
                else f"The {ref.owner} records linked to this {name} record through {ref.join}"
            )
            + f", paged, with the caller's list permissions on {ref.owner}.",
        )

    add_owner_document_routes(router, definition, manager, actor_dependency)
    return router


def _related_endpoint(
    manager: ResourceManager,
    definition: ResourceDefinition,
    relation: str,
    ref: Any,
    id_param: inspect.Parameter,
    actor_param: inspect.Parameter,
) -> Callable[..., Any]:
    owner = manager.registry.get(ref.owner)
    owner_read = owner.schemas.get("read")
    params = [
        id_param,
        _param("offset", int, Query(0, ge=0)),
        _param("limit", int, Query(50, ge=1, le=owner.max_page_size)),
        _param(
            "order_by",
            str | None,
            Query(None, description="Comma-separated fields; prefix '-' for descending"),
        ),
    ]
    for fld in owner.filterable:
        if fld == ref.field:
            continue
        annotation = owner_read.model_fields[fld].annotation if owner_read else str
        params.append(_param(f"filter__{fld}", annotation | None, Query(None, alias=fld)))  # type: ignore[operator]
    if owner.references:
        params.append(
            _param(
                "expand",
                str | None,
                Query(None, description="Relations of the listed records to include"),
            )
        )
    if definition.tenant_field:
        params.append(_param("tenant", str | None, Query(None)))
    params.append(actor_param)

    async def related_endpoint(**kw: Any) -> Any:
        order = kw.pop("order_by")
        query = ListQuery(
            offset=kw.pop("offset"),
            limit=kw.pop("limit"),
            order_by=[o for o in (order or "").split(",") if o],
            filters={
                k.removeprefix("filter__"): v
                for k, v in kw.items()
                if k.startswith("filter__") and v is not None
            },
        )
        expand = tuple(p.strip() for p in (kw.get("expand") or "").split(",") if p.strip())
        page = await manager.list_related(
            definition.name,
            kw["entity_id"],
            relation,
            query,
            actor=kw.get("actor"),
            tenant=kw.get("tenant"),
            expand=expand,
        )
        return _respond_page(
            {"items": page.items, "total": page.total, "offset": page.offset, "limit": page.limit}
        )

    return _endpoint(related_endpoint, params, f"{definition.name}_{relation}")


def _transition_endpoint(
    definition: ResourceDefinition,
    manager: ResourceManager,
    event: str,
    id_param: inspect.Parameter,
    tail: list[inspect.Parameter],
) -> Callable[..., Any]:
    schema = definition.transition_schemas.get(event)

    async def transition_endpoint(**kw: Any) -> Any:
        result = await manager.transition(
            definition.name,
            kw["entity_id"],
            event,
            kw.get("payload"),
            actor=kw["actor"],
            tenant=kw.get("tenant"),
            expected_version=parse_if_match(kw.get("if_match")),
            idempotency_key=kw.get("idempotency_key"),
        )
        return _respond(result, version_field=definition.version_field, response=kw.get("response"))

    params = [id_param]
    if schema is not None:
        params.append(_param("payload", schema, Body(...)))
    params += tail
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
