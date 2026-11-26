"""HTTP caching for ``GET``: an ``ETag`` on every JSON answer, ``304 Not
Modified`` when the client already has it (``If-None-Match``), ``Vary`` on
the credentials and the resource's ``cache_control``. See docs/caching.md.

Answers depend on the caller (permissions, field rules, tenancy), so they are
private: ``Vary: Authorization, X-API-Key`` and, unless the resource says
otherwise, ``Cache-Control: private, no-cache`` (store, but revalidate).
"""

from __future__ import annotations

import hashlib
from typing import Any

DEFAULT_CACHE_CONTROL = "private, no-cache"
VARY = "Authorization, X-API-Key"
MAX_BODY = 4 * 1024 * 1024  # larger answers pass through without an ETag


class ConditionalGetMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        request_headers = dict(scope.get("headers") or [])
        if_none_match = request_headers.get(b"if-none-match", b"").decode("latin-1")
        start: dict[str, Any] | None = None
        chunks: list[bytes] = []
        passing = False

        async def capture(message: Any) -> None:
            nonlocal start, passing
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                json = headers.get(b"content-type", b"").startswith(b"application/json")
                if message["status"] != 200 or not json:
                    passing = True
                    await send(message)
                    return
                start = message
                return
            if passing or start is None:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                if sum(map(len, chunks)) > MAX_BODY:  # too big to hash: let it go
                    passing = True
                    await send(start)
                    await send(
                        {"type": "http.response.body", "body": b"".join(chunks), "more_body": True}
                    )
                return
            await _finish(scope, send, start, b"".join(chunks), if_none_match)

        await self.app(scope, receive, capture)


async def _finish(
    scope: Any, send: Any, start: dict[str, Any], body: bytes, if_none_match: str
) -> None:
    headers = [(k, v) for k, v in start.get("headers", [])]
    names = {k.lower() for k, _ in headers}
    etag = next((v.decode("latin-1") for k, v in headers if k.lower() == b"etag"), None)
    if etag is None:
        etag = 'W/"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers.append((b"etag", etag.encode()))
    if b"cache-control" not in names:
        control = _route_cache_control(scope) or DEFAULT_CACHE_CONTROL
        headers.append((b"cache-control", control.encode()))
    if b"vary" not in names:
        headers.append((b"vary", VARY.encode()))
    if _matches(if_none_match, etag):
        kept = [
            (k, v)
            for k, v in headers
            if k.lower() in (b"etag", b"cache-control", b"vary", b"x-request-id")
            or k.lower().startswith(b"ratelimit")
        ]
        await send({"type": "http.response.start", "status": 304, "headers": kept})
        await send({"type": "http.response.body", "body": b""})
        return
    headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
    headers.append((b"content-length", str(len(body)).encode()))
    await send({**start, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _matches(if_none_match: str, etag: str) -> bool:
    """Weak comparison (RFC 9110): ``W/"x"`` matches ``"x"``."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    tag = etag.removeprefix("W/")
    return any(t.strip().removeprefix("W/") == tag for t in if_none_match.split(","))


def _route_cache_control(scope: Any) -> str | None:
    route = scope.get("route")
    return getattr(getattr(route, "endpoint", None), "cache_control", None)


__all__ = ["ConditionalGetMiddleware"]
