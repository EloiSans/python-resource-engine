"""Helpers turning HTTP failures into ``RemoteCallFailed`` (RE-E501); public for other adapters."""

from __future__ import annotations

import httpx
from resource_engine.errors import RemoteCallFailed


def check_response(response: httpx.Response, *, service: str, operation: str) -> None:
    """Raise ``RemoteCallFailed`` (RE-E501) for non-2xx responses."""
    if response.is_success:
        return
    raise RemoteCallFailed(
        f"{service} {operation} returned HTTP {response.status_code}",
        service=service,
        operation=operation,
        status=response.status_code,
        body=response.text,
        hint="Check the upstream service; details.body has the start of its response.",
    )


def transport_failed(exc: httpx.HTTPError, *, service: str, operation: str) -> RemoteCallFailed:
    return RemoteCallFailed(
        f"{service} {operation} could not be reached: {type(exc).__name__}: {exc}",
        service=service,
        operation=operation,
        hint="Check the base URL, network access and timeouts of the HTTP client.",
    )
