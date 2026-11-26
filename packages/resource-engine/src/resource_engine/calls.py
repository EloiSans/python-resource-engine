"""Outgoing calls: a span and a log line for every call to another service.

Adapters that call other systems (REST and SOAP repositories, webhook
publishers) wrap each call::

    async with outgoing_call("billing-api", "get", method="GET", url=url) as call:
        response = await client.get(url)
        call.status = response.status_code

The line goes to ``resource_engine.outgoing`` (INFO, WARNING when it failed):
service, operation, method, status, duration and outcome, with the log
context (request id, correlation id, resource, operation) of whatever caused
the call. The span is a ``client`` span under the current one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from resource_engine.tracing import get_tracer

logger = logging.getLogger("resource_engine.outgoing")


@dataclass
class OutgoingCall:
    """What the caller learns during the call (``status``)."""

    service: str
    operation: str
    method: str | None = None
    target: str | None = None
    status: int | None = None


def _target(url: str | None) -> str | None:
    """Scheme, host and path only: no query string, no credentials."""
    if not url:
        return None
    parts = urlsplit(str(url))
    if not parts.netloc:
        return parts.path or None
    return f"{parts.scheme}://{parts.hostname}{f':{parts.port}' if parts.port else ''}{parts.path}"


@asynccontextmanager
async def outgoing_call(
    service: str,
    operation: str,
    *,
    method: str | None = None,
    url: str | None = None,
) -> AsyncIterator[OutgoingCall]:
    """Span, timing and log line around one call to ``service``."""
    call = OutgoingCall(service, operation, method, _target(url))
    attributes = {"peer.service": service, "resource_engine.remote_operation": operation}
    if method:
        attributes["http.request.method"] = method
    if call.target:
        attributes["url.full"] = call.target
    started = time.perf_counter()
    error: BaseException | None = None
    try:
        async with get_tracer().span(
            f"{service} {operation}", kind="client", attributes=attributes
        ):
            yield call
    except BaseException as exc:
        error = exc
        raise
    finally:
        seconds = time.perf_counter() - started
        failed = error is not None or (call.status is not None and call.status >= 400)
        outcome = "failed" if failed else "ok"
        logger.log(
            logging.WARNING if failed else logging.INFO,
            "%s %s %s %.1f ms",
            service,
            operation,
            call.status if call.status is not None else type(error).__name__ if error else "ok",
            seconds * 1000,
            extra={
                "remote_service": service,
                "remote_operation": operation,
                "http_method": method,
                "http_status": call.status,
                "url": call.target,
                "duration_ms": round(seconds * 1000, 1),
                "outcome": outcome,
                **({"error_type": type(error).__name__} if error else {}),
            },
        )


__all__ = ["OutgoingCall", "outgoing_call"]
