"""Access log and request ids for the HTTP adapter.

``install_access_log(app)`` (or ``mount_resources(..., access_log=True)``)
writes one line per request on ``resource_engine.access``: method, route
template, status, duration, the caller and the request id. The request id
comes from ``X-Request-Id`` (or is generated), is returned in the response, and
is in the log context of everything the request does (operation logs, error
reports), so one id finds all of it.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from resource_engine.logs import bind_log_context

logger = logging.getLogger("resource_engine.access")

_VALID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class AccessLogMiddleware:
    """ASGI middleware: request ids and one access log line per HTTP request.

    Args:
        header: The request id header (accepted when it looks like an id).
        skip: Paths not logged (health checks).
        level: Log level of successful requests; 4xx log at the same level, 5xx at
            ``ERROR`` is left to the error reporter, so they log at ``WARNING``.
    """

    def __init__(
        self,
        app: Any,
        *,
        header: str = "x-request-id",
        skip: tuple[str, ...] = ("/health", "/healthz", "/ready"),
        level: int = logging.INFO,
    ) -> None:
        self.app = app
        self.header = header.lower().encode()
        self.skip = skip
        self.level = level

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sent = dict(scope.get("headers") or ())
        raw = sent.get(self.header, b"").decode("latin-1")
        request_id = raw if _VALID.match(raw) else uuid.uuid4().hex
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers") or ())
                headers.append((self.header, request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        with bind_log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                path = scope.get("path", "")
                if not path.startswith(self.skip):
                    self._log(scope, status, time.perf_counter() - started, request_id)

    def _log(self, scope: Any, status: int, seconds: float, request_id: str) -> None:
        route = getattr(scope.get("route"), "path", None) or scope.get("path", "")
        state = scope.get("state") or {}
        principal = state.get("principal") if isinstance(state, dict) else None
        level = self.level if status < 500 else logging.WARNING
        logger.log(
            level,
            "%s %s %s %.1f ms",
            scope.get("method"),
            route,
            status,
            seconds * 1000,
            extra={
                "http_method": scope.get("method"),
                "http_route": route,
                "http_status": status,
                "duration_ms": round(seconds * 1000, 1),
                "principal": getattr(principal, "id", None),
                "request_id": request_id,
            },
        )


def install_access_log(app: Any, **options: Any) -> None:
    """Add ``AccessLogMiddleware`` to a FastAPI or Starlette ``app``."""
    app.add_middleware(AccessLogMiddleware, **options)
