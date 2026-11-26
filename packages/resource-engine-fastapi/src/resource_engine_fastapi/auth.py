"""Bearer-token authentication for the generated routes.

Use the returned dependency as ``actor_dependency``::

    mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))

The engine then receives a ``Principal`` as ``actor``; with an authorizer
(``ResourceManager(authorizer=...)``) it decides what the principal may do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Authenticator, Principal


def bearer_token(request: Request) -> str | None:
    """``Authorization: Bearer <token>``, or an API key in ``X-API-Key``."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    api_key = request.headers.get("x-api-key", "").strip()
    return api_key or None


def bearer_actor(
    authenticator: Authenticator, *, required: bool = True, rate_limiter: Any = None
) -> Callable[[Request], Awaitable[Principal | None]]:
    """FastAPI dependency: ``Authorization: Bearer <token>`` → ``Principal``.

    With ``required=False`` anonymous requests get ``actor=None`` (invalid tokens
    are still rejected). With a ``RateLimiter``, callers get its ``default``
    limit, anonymous clients its ``anonymous`` one by IP, and addresses that
    failed authentication ``failed_auth`` times are refused before their
    credentials are checked (429, RE-E710; see docs/rate-limits.md). The client
    address is ``request.client.host``: behind a proxy, run uvicorn with
    ``--proxy-headers --forwarded-allow-ips`` so it is the real client's.
    """

    async def actor(request: Request) -> Principal | None:
        address = request.client.host if request.client else None
        token = bearer_token(request)
        if token is None:
            if required:
                raise AuthenticationFailed(
                    "Missing bearer token",
                    reason="missing",
                    hint="Send 'Authorization: Bearer <access token>'.",
                )
            if rate_limiter is not None:
                request.state.rate_limit = await rate_limiter.client(address)
            return None
        if rate_limiter is not None:
            await rate_limiter.before_auth(address)
        try:
            principal = await authenticator.authenticate(token)
        except AuthenticationFailed:
            if rate_limiter is not None:
                await rate_limiter.failed(address)
            raise
        request.state.principal = principal  # for the access log and error reports
        if rate_limiter is not None:
            request.state.rate_limit = await rate_limiter.caller(principal)
        return principal

    return actor
