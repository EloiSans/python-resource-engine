"""GraphQL over HTTP for FastAPI apps (``POST`` and ``GET`` on one path).

No ``from __future__ import annotations``: FastAPI reads the endpoint
annotations (``Request``) at runtime.
"""

import json
from collections.abc import Callable
from typing import Any

from resource_engine import ResourceManager, ResourceRegistry

from resource_engine_graphql.execution import (
    DEFAULT_MAX_COST,
    DEFAULT_MAX_DEPTH,
    GraphQLAdapter,
    PersistedQueries,
)


def _no_actor() -> None:
    return None


def mount_graphql(
    app: Any,
    registry: ResourceRegistry,
    manager: ResourceManager,
    *,
    path: str = "/graphql",
    actor_dependency: Callable[..., Any] = _no_actor,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_cost: int = DEFAULT_MAX_COST,
    hub: Any = None,
    persisted: PersistedQueries | None = None,
    authenticator: Any = None,
    auth_required: bool = True,
) -> GraphQLAdapter:
    """Serve the generated schema at ``path`` on a FastAPI ``app``.

    - ``POST`` with ``{"query", "variables", "operationName"}`` (JSON) runs
      queries and mutations; ``GET ?query=...`` runs queries only.
    - ``actor_dependency``: the same dependency as ``mount_resources``
      (``bearer_actor(authenticator)``); its result is the caller.
    - ``GET {path}/schema.graphql`` returns the schema in SDL.
    - ``hub`` (an ``EventHub``): adds subscriptions, served over WebSocket on
      the same path (``graphql-transport-ws``). ``authenticator`` checks the
      token the client sends in ``connection_init``; ``auth_required=False``
      lets anonymous sockets in.
    - ``max_cost`` / ``persisted``: see ``GraphQLAdapter``. ``extensions``
      (APQ) is read from the POST body or the GET parameter (JSON).

    Responses follow GraphQL over HTTP: 200 with ``{"data", "errors"}``,
    errors carrying the engine's ``code`` in ``extensions``; 400 for a body
    that is not a GraphQL request.
    """
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse, PlainTextResponse

    adapter = GraphQLAdapter(
        registry, manager, max_depth=max_depth, max_cost=max_cost, hub=hub, persisted=persisted
    )

    def bad(message: str) -> JSONResponse:
        return JSONResponse({"errors": [{"message": message}]}, status_code=400)

    async def post(request: Request, actor: Any = Depends(actor_dependency)) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return bad('The body must be JSON: {"query": ..., "variables": {...}}')
        if not isinstance(body, dict):
            return bad("Missing 'query'")
        query, extensions = body.get("query"), body.get("extensions")
        if query is not None and not isinstance(query, str):
            return bad("'query' must be a string")
        if extensions is not None and not isinstance(extensions, dict):
            return bad("'extensions' must be an object")
        if query is None and not extensions:
            return bad("Missing 'query'")
        variables = body.get("variables")
        if variables is not None and not isinstance(variables, dict):
            return bad("'variables' must be an object")
        result = await adapter.execute(
            query, variables, body.get("operationName"), actor=actor, extensions=extensions
        )
        return JSONResponse(result)

    async def get(request: Request, actor: Any = Depends(actor_dependency)) -> JSONResponse:
        query = request.query_params.get("query")
        try:
            raw = request.query_params.get("variables")
            variables = json.loads(raw) if raw else None
            raw = request.query_params.get("extensions")
            extensions = json.loads(raw) if raw else None
        except ValueError:
            return bad("'variables' and 'extensions' must be JSON")
        if not query and not extensions:
            return bad("Missing 'query'")
        document = adapter.prepare(
            query, variables, request.query_params.get("operationName"), extensions
        )
        if not isinstance(document, dict) and _is_mutation(adapter, document, request):
            return JSONResponse({"errors": [{"message": "Mutations need POST"}]}, status_code=405)
        result = await adapter.execute(
            query,
            variables,
            request.query_params.get("operationName"),
            actor=actor,
            extensions=extensions,
        )
        return JSONResponse(result)

    async def sdl() -> PlainTextResponse:
        return PlainTextResponse(adapter.sdl())

    app.add_api_route(path, post, methods=["POST"], include_in_schema=False)
    if hub is not None:
        from fastapi import WebSocket

        from resource_engine_graphql.ws import serve_websocket

        async def socket(websocket: WebSocket) -> None:
            await serve_websocket(
                websocket, adapter, authenticator=authenticator, required=auth_required
            )

        app.add_api_websocket_route(path, socket)
    app.add_api_route(path, get, methods=["GET"], include_in_schema=False)
    app.add_api_route(f"{path}/schema.graphql", sdl, methods=["GET"], include_in_schema=False)
    return adapter


def _is_mutation(adapter: GraphQLAdapter, document: Any, request: Any) -> bool:
    from graphql import get_operation_ast

    operation = get_operation_ast(document, request.query_params.get("operationName"))
    return operation is not None and operation.operation.value != "query"
