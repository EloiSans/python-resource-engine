"""Generate FastAPI routes from registered resources.

FastAPI only adapts HTTP to ``ResourceManager`` calls: no business logic lives here.
Endpoints are built with explicit signatures so that FastAPI validates bodies with
the resource's Pydantic schemas and documents them in OpenAPI.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Union

from fastapi import APIRouter, Body, Depends, FastAPI, Header, Path, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, create_model
from resource_engine import ListQuery, ResourceDefinition, ResourceManager, ResourceRegistry
from resource_engine.application.manager import EXPAND_PAGE_SIZE
from resource_engine.application.relations import dump_read, needs_dump
from resource_engine.errors import InvalidQuery
from resource_engine.versions import ApiVersion, Deprecation

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


def _page_model(read: Any, name: str | None = None) -> type[BaseModel]:
    return create_model(
        f"{name or read.__name__}Page",
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
    api_version: ApiVersion | None = None,
    deprecation: Deprecation | None = None,
) -> APIRouter:
    """Routes for one resource: CRUD, ``/transitions/<event>`` and ``/actions/<name>``.

    ``actor_dependency`` is a FastAPI dependency whose result is passed to the
    manager as ``actor``. It is the integration point for authentication; the
    engine does not interpret it yet.

    ``api_version``: serve an older version of the resource's API (its
    schemas, converting input and output); ``deprecation`` marks the routes
    deprecated in the OpenAPI document (docs/versioning.md).
    """
    name = definition.name
    deprecation = deprecation or definition.resource_cls.deprecation
    router = APIRouter(
        prefix=f"/{name}",
        tags=[name],
        deprecated=True if deprecation is not None else None,
    )
    schemas = (
        {
            role: api_version.schema(role, definition.schemas)
            for role in ("read", "create", "update")
            if api_version.schema(role, definition.schemas) is not None
        }
        if api_version is not None
        else definition.schemas
    )
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
    read = schemas.get("read")
    # a base resource answers with each record's subtype schema
    subtypes = (
        [d.schemas["read"] for d in registry_of(manager).subtypes(name).values()]
        if api_version is None
        else []
    )
    out: Any = Union[tuple(subtypes)] if subtypes else read  # noqa: UP007
    to_read = api_version.to_read if api_version is not None else None
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
        if to_read is not None:  # an older version: its shape, from the current one
            converted = JSONResponse(to_read(_dump(value)), status_code=status_code)
            record_version = getattr(value, version_field, None) if version_field else None
            if record_version is not None:
                converted.headers["ETag"] = f'"{record_version}"'
            return converted
        return _respond(
            value, status_code, version_field=version_field, response=kw.get("response")
        )

    def given(payload: Any, convert: Any) -> Any:
        """An older version's input as the current schema's input."""
        if convert is None or api_version is None:
            return payload
        return convert(payload.model_dump(exclude_unset=True))

    if definition.exposed("list"):
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
            if to_read is not None:
                return JSONResponse(
                    {
                        "items": [to_read(_dump(i)) for i in page.items],
                        "total": page.total,
                        "offset": page.offset,
                        "limit": page.limit,
                    }
                )
            return _respond_page(
                {
                    "items": page.items,
                    "total": page.total,
                    "offset": page.offset,
                    "limit": page.limit,
                }
            )

        list_endpoint.cache_control = definition.cache_control  # type: ignore[attr-defined]
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
            response_model=_page_model(out, read.__name__) if read else None,
            summary=_summary(docs.get("list")) or f"List {name}",
            description=docs.get("list"),
        )

        async def export_endpoint(**kw: Any) -> Any:
            from resource_engine.exports import export_table

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
                order_by=[o for o in (kw.get("order_by") or "").split(",") if o],
                filters=filters,
            )
            content, media_type, filename = await export_table(
                manager,
                name,
                query,
                format=kw["format"],
                actor=kw.get("actor"),
                tenant=kw.get("tenant"),
            )
            return Response(
                content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        router.add_api_route(
            "/export",
            _endpoint(
                export_endpoint,
                [
                    _param(
                        "format",
                        Literal["csv", "xlsx"],
                        Query("csv", description="csv (UTF-8, with a BOM for Excel) or xlsx"),
                    ),
                    _param(
                        "order_by",
                        str | None,
                        Query(
                            None, description="Comma-separated fields; prefix '-' for descending"
                        ),
                    ),
                    *filter_params,
                    *tenant_params,
                    actor_param,
                ],
                f"export_{name}",
            ),
            methods=["GET"],
            response_class=Response,
            responses={
                200: {
                    "content": {
                        "text/csv": {},
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                    },
                    "description": "The list as a file, with the caller's permissions",
                }
            },
            summary=f"Export {name} as CSV or XLSX",
            description="The list with the same filters, sorting and permissions, as a file "
            "(at most 10 000 rows).",
        )

    if definition.exposed("get"):

        async def get_endpoint(**kw: Any) -> Any:
            result = await manager.get(
                name,
                kw["entity_id"],
                actor=kw["actor"],
                tenant=kw.get("tenant"),
                expand=expand_of(kw),
            )
            return reply(result, kw)

        get_endpoint.cache_control = definition.cache_control  # type: ignore[attr-defined]
        router.add_api_route(
            "/{entity_id}",
            _endpoint(
                get_endpoint,
                [id_param, *expand_params, *tenant_params, actor_param, response_param],
                f"get_{name}",
            ),
            methods=["GET"],
            response_model=out,
            summary=_summary(docs.get("get")) or f"Get one of {name}",
            description=docs.get("get"),
        )

    if definition.exposed("create"):

        async def create_endpoint(**kw: Any) -> Any:
            result = await manager.create(
                name,
                given(kw["payload"], api_version.from_create if api_version else None),
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
                    _param("payload", schemas["create"], Body(...)),
                    *tenant_params,
                    *key_params,
                    actor_param,
                    response_param,
                ],
                f"create_{name}",
            ),
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_model=out,
            summary=_summary(docs.get("create")) or f"Create {name}",
            description=docs.get("create"),
        )

    if definition.exposed("update"):

        async def update_endpoint(**kw: Any) -> Any:
            result = await manager.update(
                name,
                kw["entity_id"],
                given(kw["payload"], api_version.from_update if api_version else None),
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
                    _param("payload", schemas["update"], Body(...)),
                    *tenant_params,
                    *match_params,
                    *key_params,
                    actor_param,
                    response_param,
                ],
                f"update_{name}",
            ),
            methods=["PATCH"],
            response_model=out,
            summary=_summary(docs.get("update")) or f"Update {name}",
            description=docs.get("update"),
        )

    if definition.exposed("delete"):

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

            several = len(definition.machines) > 1
            machine_params = (
                [
                    inspect.Parameter(
                        "machine",
                        inspect.Parameter.KEYWORD_ONLY,
                        default=Query(None, description="Only this state machine"),
                        annotation=Literal[tuple(definition.machines)] | None,  # type: ignore[valid-type]
                    )
                ]
                if several
                else []
            )

            async def history_endpoint(
                entity_id: Any, actor: Any, tenant: Any = None, machine: Any = None
            ) -> Any:
                return await manager.history(
                    name, entity_id, actor=actor, tenant=tenant, machine=machine
                )

            router.add_api_route(
                "/{entity_id}/history",
                _endpoint(
                    history_endpoint,
                    [id_param, *tenant_params, *machine_params, actor_param],
                    f"history_{name}",
                ),
                methods=["GET"],
                response_model=list[StateChange],
                summary="State history",
                description="The record's creation and committed transitions, oldest "
                "first, with who made them and when (from the audit trail).",
            )
        for event in definition.transitions:
            owner_machine = definition.machine_for(event)
            sm_doc = inspect.getdoc(owner_machine.machine_cls) if owner_machine else ""
            router.add_api_route(
                f"/{{entity_id}}/transitions/{event}",
                _transition_endpoint(
                    definition,
                    manager,
                    event,
                    id_param,
                    [*tenant_params, *match_params, *key_params, actor_param, response_param],
                    reply,
                ),
                methods=["POST"],
                response_model=out,
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
        response_model = action.output_schema or (out if action.instance else None)
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
    if deprecation is not None:  # the sunset, for clients and the contract check
        for route in router.routes:
            extra = dict(getattr(route, "openapi_extra", None) or {})
            extra["x-deprecation"] = deprecation.to_dict()
            route.openapi_extra = extra  # type: ignore[attr-defined]
            if hasattr(route, "description"):
                text = route.description or ""  # type: ignore[attr-defined]
                route.description = (  # type: ignore[attr-defined]
                    f"{deprecation.notice()}\n\n{text}" if text else deprecation.notice()
                )
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
    reply: Callable[[Any, dict[str, Any]], Any] | None = None,
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
        if reply is not None:
            return reply(result, kw)
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
    version: str | None = None,
    current: bool = True,
    deprecation: Deprecation | None = None,
) -> APIRouter:
    """Routes for every registered resource (and document downloads, if enabled).

    ``redirect_downloads`` answers downloads with a 307 to a presigned URL instead
    of streaming the content through the API.

    ``version``: the API version these routes serve. An older one (``current``
    false) uses each resource's ``api_versions[version]`` where it has one;
    ``deprecation`` deprecates the whole version.
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
        api = (
            definition.resource_cls.api_versions.get(version)
            if version is not None and not current
            else None
        )
        router.include_router(
            build_resource_router(
                definition,
                manager,
                actor_dependency=actor_dependency,
                api_version=api,
                deprecation=(api.deprecation if api else None) or deprecation,
            )
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
    access_log: bool = False,
    http_cache: bool = True,
    versions: Sequence[str] = (),
    deprecated: Mapping[str, Deprecation] | None = None,
) -> None:
    """Include every registered resource in ``app`` and install error handlers.
    ``access_log`` adds the access log and request ids (``install_access_log``);
    ``http_cache`` the ETag / 304 handling of ``GET`` (docs/caching.md).

    ``versions`` (``["v1", "v2"]``, oldest first) serves each under its own
    prefix (``/v1/...``); the last is the current one, and older ones use the
    resources' ``api_versions``. ``deprecated`` deprecates whole versions.
    Deprecated routes answer with ``Deprecation``, ``Sunset`` and ``Link``
    headers (docs/versioning.md)."""
    deprecated = dict(deprecated or {})
    unknown = sorted(set(deprecated) - set(versions))
    if unknown:
        from resource_engine.errors import ConfigurationError

        raise ConfigurationError(
            f"Deprecated API versions {unknown} are not served",
            hint=f"Served versions: {list(versions)}",
            doc="docs/versioning.md",
        )
    notices: dict[str, Deprecation] = {}
    for index, version in enumerate(versions or [None]):  # type: ignore[list-item]
        current = index == len(versions) - 1 if versions else True
        version_prefix = f"{prefix}/{version}" if version else prefix
        whole = deprecated.get(version) if version else None
        app.include_router(
            build_router(
                registry,
                manager,
                prefix=version_prefix,
                actor_dependency=actor_dependency,
                redirect_downloads=redirect_downloads,
                version=version,
                current=current,
                deprecation=whole,
            )
        )
        for definition in registry:
            api = definition.resource_cls.api_versions.get(version) if not current else None
            notice = (
                (api.deprecation if api else None) or whole or definition.resource_cls.deprecation
            )
            if notice is not None:
                notices[f"{version_prefix}/{definition.name}"] = notice
    if notices:
        from resource_engine_fastapi.versioning import DeprecationMiddleware

        app.add_middleware(DeprecationMiddleware, notices=notices)
    install_exception_handlers(app)
    if http_cache:
        from resource_engine_fastapi.caching import ConditionalGetMiddleware

        app.add_middleware(ConditionalGetMiddleware)
    if access_log:
        from resource_engine_fastapi.access import install_access_log

        install_access_log(app)
