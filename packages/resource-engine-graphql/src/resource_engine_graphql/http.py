"""GraphQL over HTTP for FastAPI apps (``POST`` and ``GET`` on one path).

No ``from __future__ import annotations``: FastAPI reads the endpoint
annotations (``Request``) at runtime.
"""

import json
from collections.abc import Callable
from typing import Any

from resource_engine import ResourceManager, ResourceRegistry

from resource_engine_graphql.execution import DEFAULT_MAX_DEPTH, GraphQLAdapter


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
) -> GraphQLAdapter:
    """Serve the generated schema at ``path`` on a FastAPI ``app``.

    - ``POST`` with ``{"query", "variables", "operationName"}`` (JSON) runs
      queries and mutations; ``GET ?query=...`` runs queries only.
    - ``actor_dependency``: the same dependency as ``mount_resources``
      (``bearer_actor(authenticator)``); its result is the caller.
    - ``GET {path}/schema.graphql`` returns the schema in SDL.

    Responses follow GraphQL over HTTP: 200 with ``{"data", "errors"}``,
    errors carrying the engine's ``code`` in ``extensions``; 400 for a body
    that is not a GraphQL request.
    """
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse, PlainTextResponse

    adapter = GraphQLAdapter(registry, manager, max_depth=max_depth)

    def bad(message: str) -> JSONResponse:
        return JSONResponse({"errors": [{"message": message}]}, status_code=400)

    async def post(request: Request, actor: Any = Depends(actor_dependency)) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return bad('The body must be JSON: {"query": ..., "variables": {...}}')
        if not isinstance(body, dict) or not isinstance(body.get("query"), str):
            return bad("Missing 'query'")
        variables = body.get("variables")
        if variables is not None and not isinstance(variables, dict):
            return bad("'variables' must be an object")
        result = await adapter.execute(
            body["query"], variables, body.get("operationName"), actor=actor
        )
        return JSONResponse(result)

    async def get(request: Request, actor: Any = Depends(actor_dependency)) -> JSONResponse:
        query = request.query_params.get("query")
        if not query:
            return bad("Missing 'query'")
        if query.lstrip().startswith("mutation"):
            return JSONResponse({"errors": [{"message": "Mutations need POST"}]}, status_code=405)
        raw = request.query_params.get("variables")
        try:
            variables = json.loads(raw) if raw else None
        except ValueError:
            return bad("'variables' must be JSON")
        result = await adapter.execute(
            query, variables, request.query_params.get("operationName"), actor=actor
        )
        return JSONResponse(result)

    async def sdl() -> PlainTextResponse:
        return PlainTextResponse(adapter.sdl())

    app.add_api_route(path, post, methods=["POST"], include_in_schema=False)
    app.add_api_route(path, get, methods=["GET"], include_in_schema=False)
    app.add_api_route(f"{path}/schema.graphql", sdl, methods=["GET"], include_in_schema=False)
    return adapter
