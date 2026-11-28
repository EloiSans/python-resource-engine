"""Deprecation headers for deprecated API versions and resources
(``Deprecation``, RFC 9745; ``Sunset``, RFC 8594; ``Link: rel="deprecation"``).
``mount_resources(..., versions=..., deprecated=...)`` installs it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from resource_engine.versions import Deprecation


class DeprecationMiddleware:
    """ASGI middleware adding the notice of the longest matching path prefix."""

    def __init__(self, app: Any, notices: Mapping[str, Deprecation]) -> None:
        self.app = app
        self.notices = sorted(
            ((prefix.rstrip("/"), notice.headers()) for prefix, notice in notices.items()),
            key=lambda item: -len(item[0]),
        )

    def headers_for(self, path: str) -> dict[str, str] | None:
        for prefix, headers in self.notices:
            if path == prefix or path.startswith(prefix + "/"):
                return headers
        return None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = self.headers_for(scope.get("path", ""))
        if headers is None:
            await self.app(scope, receive, send)
            return
        extra = [(k.lower().encode(), v.encode()) for k, v in headers.items()]

        async def sending(message: Any) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, sending)


__all__ = ["DeprecationMiddleware"]
