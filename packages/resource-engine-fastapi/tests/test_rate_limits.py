"""Rate limits over HTTP: 429 with Retry-After and RateLimit headers, per caller,
per anonymous IP, and failed authentications per IP."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Principal
from resource_engine.ratelimit import (
    InMemoryRateLimitStore,
    Limit,
    SlidingWindowLimiter,
)
from resource_engine_fastapi import bearer_actor, install_exception_handlers


class Tokens:
    def __init__(self) -> None:
        self.checked = 0

    async def authenticate(self, token: str) -> Principal:
        self.checked += 1
        if token.startswith("good-"):
            return Principal(subject=token, issuer="i")
        raise AuthenticationFailed("Invalid token", reason="invalid")


def _app(tokens, **limits):
    limiter = SlidingWindowLimiter(InMemoryRateLimitStore(), **limits)
    app = FastAPI()
    install_exception_handlers(app)
    required = bearer_actor(tokens, rate_limiter=limiter)
    optional = bearer_actor(tokens, required=False, rate_limiter=limiter)

    @app.get("/private")
    async def private(actor=Depends(required)):
        return {"who": actor.subject}

    @app.get("/public")
    async def public(actor=Depends(optional)):
        return {"who": getattr(actor, "subject", None)}

    return app


def test_per_caller_with_headers():
    with TestClient(_app(Tokens(), default=Limit(2, per=60))) as c:
        ok = c.get("/private", headers={"Authorization": "Bearer good-a"})
        assert ok.status_code == 200
        assert (ok.headers["ratelimit-limit"], ok.headers["ratelimit-remaining"]) == ("2", "1")
        c.get("/private", headers={"Authorization": "Bearer good-a"})
        refused = c.get("/private", headers={"Authorization": "Bearer good-a"})
        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "RE-E710"
        assert int(refused.headers["retry-after"]) >= 1
        assert refused.headers["ratelimit-remaining"] == "0"
        assert c.get("/private", headers={"Authorization": "Bearer good-b"}).status_code == 200


def test_anonymous_clients_by_address():
    with TestClient(_app(Tokens(), anonymous=Limit(1, per=60))) as c:
        assert c.get("/public").status_code == 200
        assert c.get("/public").status_code == 429
        assert c.get("/public", headers={"Authorization": "Bearer good-a"}).status_code == 200


def test_brute_force_is_stopped_before_checking_credentials():
    tokens = Tokens()
    with TestClient(_app(tokens, failed_auth=Limit(3, per=300))) as c:
        for _ in range(3):
            assert c.get("/private", headers={"X-API-Key": "rek_x_guess"}).status_code == 401
        blocked = c.get("/private", headers={"Authorization": "Bearer good-a"})
        assert blocked.status_code == 429  # even a valid token waits: the address is blocked
        assert tokens.checked == 3
        assert blocked.json()["error"]["details"]["scope"] == "failed authentications"
