"""Serve the AsyncAPI document next to OpenAPI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from resource_engine import ResourceRegistry
from resource_engine.messaging import EventRouter
from resource_engine.messaging.asyncapi import asyncapi_document


def mount_asyncapi(
    app: FastAPI,
    registry: ResourceRegistry,
    *,
    path: str = "/asyncapi.json",
    consumers: Sequence[EventRouter] = (),
    document: Mapping[str, Any] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Serve the AsyncAPI 3.0 document of the app's events at ``path``.

    The document is built once, from ``registry`` and ``consumers`` with the
    ``asyncapi_document`` ``options`` (``title``, ``servers``, ``channels``,
    ``format``...), or given as ``document`` (for example the file written by
    ``resource-engine docs asyncapi``). The title and version default to the
    app's. Returns the document.
    """
    if document is None:
        options.setdefault("title", app.title)
        options.setdefault("version", app.version)
        document = asyncapi_document(registry, consumers=consumers, **options)
    body = dict(document)

    async def asyncapi() -> JSONResponse:
        return JSONResponse(body)

    app.add_api_route(
        path,
        asyncapi,
        methods=["GET"],
        include_in_schema=False,
        summary="AsyncAPI document",
    )
    return body
