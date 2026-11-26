"""The MCP server: tools from the registry, calls through ``ResourceManager``.

```python
mcp = ResourceMcpServer(registry, manager)
await mcp.run_stdio(actor=principal)              # local agent, fixed identity
mcp.mount(app, authenticator=oidc, resource_url="https://api.example.com/mcp",
          issuer_url="https://login.example.com/realms/acme")   # HTTP, bearer tokens
```

The server adds no business logic: every call is a manager operation with the
caller as ``actor``, so permissions, tenancy, validation, hooks and the audit
trail apply exactly as over HTTP. See docs/mcp.md.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from typing import Any

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from resource_engine import (
    DomainError,
    ListQuery,
    ResourceEngineError,
    ResourceManager,
    ResourceRegistry,
)
from resource_engine.application.relations import dump_read
from resource_engine.errors import ValidationFailed
from resource_engine.ports.identity import Authenticator

from resource_engine_mcp.tools import ResourceTool, build_tools

logger = logging.getLogger("resource_engine.mcp")

type ActorResolver = Callable[[ServerRequestContext[Any, Any]], Any | Awaitable[Any]]


# ------------------------------------------------------------------ identity


class PrincipalAccessToken(AccessToken):
    """An MCP access token carrying the engine's ``Principal``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    principal: Any = None


class PrincipalVerifier:
    """MCP ``TokenVerifier`` over the engine's ``Authenticator`` (OIDC...).

    Invalid tokens answer 401 with ``WWW-Authenticate`` before any tool runs;
    valid ones carry the ``Principal`` to the tool calls.
    """

    def __init__(self, authenticator: Authenticator) -> None:
        self.authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = await self.authenticator.authenticate(token)
        except ResourceEngineError:
            return None
        if principal is None:
            return None
        claims = dict(getattr(principal, "claims", {}) or {})
        audience = claims.get("aud")
        expires = claims.get("exp")
        return PrincipalAccessToken(
            token=token,
            client_id=getattr(principal, "client_id", None) or principal.subject,
            scopes=sorted(getattr(principal, "scopes", ()) or ()),
            expires_at=int(expires) if isinstance(expires, int | float) else None,
            resource=audience if isinstance(audience, str) else None,
            subject=principal.subject,
            claims={"iss": principal.issuer},
            principal=principal,
        )


def token_principal(ctx: ServerRequestContext[Any, Any]) -> Any:
    """The ``Principal`` of the request's bearer token (HTTP), or ``None``."""
    request = ctx.request
    scope = getattr(request, "scope", None)
    user = scope.get("user") if isinstance(scope, Mapping) else None
    token = getattr(user, "access_token", None)
    return getattr(token, "principal", None)


# --------------------------------------------------------------------- results


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return dump_read(value)
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    return TypeAdapter(Any).dump_python(value, mode="json")


def _result(structured: Any) -> types.CallToolResult:
    text = json.dumps(structured, ensure_ascii=False, default=str)
    return types.CallToolResult(
        content=[types.TextContent(text=text)], structured_content=structured
    )


def _error(payload: dict[str, Any]) -> types.CallToolResult:
    body = {"error": payload}
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(body, ensure_ascii=False, default=str))],
        structured_content=body,
        is_error=True,
    )


# ---------------------------------------------------------------------- server


class ResourceMcpServer:
    """An MCP server exposing the registry's operations as tools.

    Args:
        registry / manager: The application's registry and manager.
        name: Server name shown to clients (default ``resource-engine``).
        instructions: Text for the agent; defaults to a summary of the resources.
        resources: Only these resources (default: every registered resource).
        include / exclude: Tool names or shell patterns (``invoices_*``,
            ``*_delete``) to keep or leave out; ``exclude`` wins.
        filter_tools: List only the tools the caller may use (``manager.can``
            without a record). Calls are always checked by the manager.
        actor: Fixed caller for every request (stdio, service accounts).
        resolve_actor: ``(ctx) -> actor`` (sync or async) for any other
            identity scheme; by default the bearer token's ``Principal`` (HTTP),
            then ``actor``.
    """

    def __init__(
        self,
        registry: ResourceRegistry,
        manager: ResourceManager,
        *,
        name: str = "resource-engine",
        version: str = "",
        instructions: str | None = None,
        resources: Collection[str] | None = None,
        include: Collection[str] | None = None,
        exclude: Collection[str] = (),
        filter_tools: bool = True,
        actor: Any = None,
        resolve_actor: ActorResolver | None = None,
    ) -> None:
        self.registry = registry
        self.manager = manager
        self.actor = actor
        self.filter_tools = filter_tools
        self.resolve_actor = resolve_actor
        self.tools = build_tools(
            registry, manager, resources=resources, include=include, exclude=exclude
        )
        self.server: Server[Any] = Server(
            name,
            version=version,
            instructions=instructions if instructions is not None else self._instructions(),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )

    # ---------------------------------------------------------------- tools

    def _instructions(self) -> str:
        names = sorted({t.resource for t in self.tools.values()})
        lines = [
            "Tools for the application's business records. Each call runs with your "
            "permissions and is audited. Read a record (`<resource>_get`) and its "
            "allowed transitions before changing it; send `idempotency_key` on writes "
            "you may retry.",
            "",
            "Resources:",
        ]
        for resource in names:
            doc = inspect.getdoc(self.registry.get(resource).resource_cls) or ""
            summary = doc.strip().splitlines()[0] if doc.strip() else ""
            lines.append(f"- {resource}" + (f": {summary}" if summary else ""))
        return "\n".join(lines)

    async def actor_for(self, ctx: ServerRequestContext[Any, Any]) -> Any:
        if self.resolve_actor is not None:
            actor = self.resolve_actor(ctx)
            return await actor if inspect.isawaitable(actor) else actor
        principal = token_principal(ctx)
        return principal if principal is not None else self.actor

    async def visible_tools(self, actor: Any) -> list[ResourceTool]:
        """The tools ``actor`` may use (all of them with ``filter_tools=False``)."""
        if not self.filter_tools:
            return list(self.tools.values())
        visible = []
        for tool in self.tools.values():
            try:
                allowed = await self.manager.can(
                    actor, tool.permission_resource or tool.resource, tool.permission
                )
            except ResourceEngineError:
                allowed = False
            if allowed:
                visible.append(tool)
        return visible

    async def _list_tools(
        self, ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        actor = await self.actor_for(ctx)
        return types.ListToolsResult(tools=[t.to_mcp() for t in await self.visible_tools(actor)])

    async def _call_tool(
        self, ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        tool = self.tools.get(params.name)
        if tool is None:
            return _error(
                {
                    "message": f"Unknown tool {params.name!r}",
                    "hint": "List the tools again: they depend on your permissions.",
                }
            )
        actor = await self.actor_for(ctx)
        return await self.call(tool, params.arguments or {}, actor=actor)

    async def call(
        self, tool: ResourceTool, arguments: Mapping[str, Any], *, actor: Any = None
    ) -> types.CallToolResult:
        """Run ``tool`` with ``arguments`` as ``actor``. Engine and domain errors
        come back as tool errors (``is_error``) carrying the error's code, message
        and hint, so the agent can correct itself. ``actor`` defaults to the
        server's fixed actor."""
        if actor is None:
            actor = self.actor
        try:
            value = await self._dispatch(tool, dict(arguments), actor)
        except (ResourceEngineError, DomainError) as exc:
            await _report(exc, tool, actor)  # only engine failures (RE-E501...)
            return _error(exc.to_dict())
        except Exception as exc:
            await _report(exc, tool, actor)
            return _error({"code": "RE-E000", "message": "Internal error"})
        return _result(value)

    async def _dispatch(self, tool: ResourceTool, args: dict[str, Any], actor: Any) -> Any:
        m = self.manager
        definition = self.registry.get(tool.resource)
        kw: dict[str, Any] = {"actor": actor, "tenant": args.pop("tenant", None)}
        entity_id = _id(definition.id_type, args.pop("id", None)) if tool.instance else None
        data = args.pop("data", None)
        version = args.pop("expected_version", None)
        key = args.pop("idempotency_key", None)
        expand = tuple(args.pop("expand", None) or ())
        op = tool.operation

        if op == "related":
            owner = self.registry.get(tool.permission_resource)  # type: ignore[arg-type]
            query = ListQuery(
                offset=args.pop("offset", 0),
                limit=args.pop("limit", 50),
                order_by=list(args.pop("order_by", None) or ()),
                filters=_filters(owner, args.pop("filters", None) or {}, self.registry),
            )
            _no_extra(tool, args)
            page = await m.list_related(
                tool.resource, entity_id, tool.target, query, expand=expand, **kw
            )
            return {
                "items": _dump(page.items),
                "total": page.total,
                "offset": page.offset,
                "limit": page.limit,
            }
        if op == "list":
            filters = _filters(definition, args.pop("filters", None) or {}, self.registry)
            query = ListQuery(
                offset=args.pop("offset", 0),
                limit=args.pop("limit", 50),
                order_by=list(args.pop("order_by", None) or ()),
                filters=filters,
            )
            _no_extra(tool, args)
            page = await m.list(tool.resource, query, expand=expand, **kw)
            return {
                "items": _dump(page.items),
                "total": page.total,
                "offset": page.offset,
                "limit": page.limit,
            }
        _no_extra(tool, args)
        if op == "get":
            return _dump(await m.get(tool.resource, entity_id, expand=expand, **kw))
        if op == "allowed_transitions":
            return {"transitions": await m.allowed_transitions(tool.resource, entity_id, **kw)}
        if op == "history":
            return {"changes": _dump(await m.history(tool.resource, entity_id, **kw))}
        if op == "create":
            return _dump(await m.create(tool.resource, data, idempotency_key=key, **kw))
        if op == "update":
            return _dump(
                await m.update(
                    tool.resource,
                    entity_id,
                    data,
                    expected_version=version,
                    idempotency_key=key,
                    **kw,
                )
            )
        if op == "delete":
            await m.delete(
                tool.resource, entity_id, expected_version=version, idempotency_key=key, **kw
            )
            return {"id": _dump(entity_id), "deleted": True}
        if op == "transition":
            return _dump(
                await m.transition(
                    tool.resource,
                    entity_id,
                    tool.target,
                    data,
                    expected_version=version,
                    idempotency_key=key,
                    **kw,
                )
            )
        if op == "action":
            result = _dump(
                await m.run_action(
                    tool.resource,
                    tool.target,
                    entity_id,
                    data,
                    expected_version=version,
                    idempotency_key=key,
                    **kw,
                )
            )
            return {"result": result} if tool.wraps_result else result
        raise AssertionError(f"unknown operation {op}")

    # ------------------------------------------------------------ transports

    async def run_stdio(self, *, actor: Any = None) -> None:
        """Serve one client over stdin/stdout (``actor`` overrides the fixed one)."""
        from mcp.server.stdio import stdio_server

        if actor is not None:
            self.actor = actor
        async with stdio_server() as (read, write):
            await self.server.run(read, write, self.server.create_initialization_options())

    def http_app(
        self,
        *,
        path: str = "/mcp",
        authenticator: Authenticator | None = None,
        resource_url: str | None = None,
        issuer_url: str | None = None,
        required_scopes: list[str] | None = None,
        **options: Any,
    ) -> Any:
        """A Starlette app serving Streamable HTTP at ``path``.

        With ``authenticator``, requests need ``Authorization: Bearer <token>``
        (401 otherwise) and the token's ``Principal`` is the actor;
        ``resource_url`` (this endpoint's public URL) and ``issuer_url`` (the
        authorization server) publish the OAuth protected resource metadata
        MCP clients use to sign in. Other ``options`` go to the SDK's
        ``streamable_http_app`` (``stateless_http``, ``json_response``,
        ``transport_security``, ``host``...).
        """
        verifier = None
        auth = None
        if authenticator is not None:
            verifier = PrincipalVerifier(authenticator)
            if issuer_url is not None:
                auth = AuthSettings(
                    issuer_url=issuer_url,  # type: ignore[arg-type]
                    resource_server_url=resource_url,  # type: ignore[arg-type]
                    required_scopes=required_scopes,
                    # the Authenticator checks the audience (OIDC ``audience=``)
                    validate_token_resource=False,
                )
        return self.server.streamable_http_app(
            streamable_http_path=path, token_verifier=verifier, auth=auth, **options
        )

    def mount(self, app: Any, **http_options: Any) -> Any:
        """Add the HTTP endpoint to a Starlette or FastAPI ``app`` (after its own
        routes) and run the MCP session manager in the app's lifespan. Takes
        the ``http_app`` options; returns the MCP app."""
        mcp_app = self.http_app(**http_options)
        app.mount("", mcp_app)
        previous = app.router.lifespan_context
        manager = self.server.session_manager

        @asynccontextmanager
        async def lifespan(parent: Any) -> AsyncIterator[Any]:
            async with manager.run(), previous(parent) as state:
                yield state

        app.router.lifespan_context = lifespan
        return mcp_app


async def _report(exc: BaseException, tool: ResourceTool, actor: Any) -> None:
    from resource_engine.reporting import report_error

    await report_error(exc, where="mcp", actor=actor, route=tool.name)


# ---------------------------------------------------------------- arguments


def _id(id_type: Any, value: Any) -> Any:
    if value is None:
        raise ValidationFailed("The tool needs an 'id'", hint="Pass the record's id as 'id'.")
    try:
        return TypeAdapter(id_type).validate_python(value)
    except ValidationError as exc:
        raise ValidationFailed(
            f"Invalid id {value!r}",
            details=exc.errors(include_url=False, include_context=False),
        ) from exc


def _filters(definition: Any, filters: Mapping[str, Any], registry: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in filters.items():
        if value is None:
            continue
        owner, field_name = definition, name
        relation, dot, rest = name.partition(".")
        if dot and relation in definition.references:
            owner, field_name = registry.get(definition.references[relation].target), rest
        read = owner.schemas.get("read")
        field = read.model_fields.get(field_name) if read else None
        if field is None:
            out[name] = value  # the manager refuses unknown filters (RE-E103)
            continue
        try:
            out[name] = TypeAdapter(field.annotation).validate_python(value)
        except ValidationError as exc:
            raise ValidationFailed(
                f"Invalid filter {name}={value!r}",
                details=exc.errors(include_url=False, include_context=False),
            ) from exc
    return out


def _no_extra(tool: ResourceTool, args: Mapping[str, Any]) -> None:
    if args:
        raise ValidationFailed(
            f"Unknown arguments for {tool.name}: {', '.join(sorted(args))}",
            hint="See the tool's input schema.",
        )
