"""Bearer-token authentication for the generated routes.

Use the returned dependency as ``actor_dependency``::

    mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))

The engine then receives a ``Principal`` as ``actor``; with an authorizer
(``ResourceManager(authorizer=...)``) it decides what the principal may do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Authenticator, Principal


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def bearer_actor(
    authenticator: Authenticator, *, required: bool = True
) -> Callable[[Request], Awaitable[Principal | None]]:
    """FastAPI dependency: ``Authorization: Bearer <token>`` → ``Principal``.

    With ``required=False`` anonymous requests get ``actor=None`` (invalid tokens
    are still rejected).
    """

    async def actor(request: Request) -> Principal | None:
        token = bearer_token(request)
        if token is None:
            if required:
                raise AuthenticationFailed(
                    "Missing bearer token",
                    reason="missing",
                    hint="Send 'Authorization: Bearer <access token>'.",
                )
            return None
        return await authenticator.authenticate(token)

    return actor
