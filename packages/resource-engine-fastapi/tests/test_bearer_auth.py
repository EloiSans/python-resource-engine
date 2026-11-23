from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Principal
from resource_engine_fastapi import bearer_actor, install_exception_handlers


class FakeAuthenticator:
    async def authenticate(self, token: str) -> Principal:
        if token != "good":
            raise AuthenticationFailed("bad token", reason="invalid_signature")
        return Principal(subject="u1", issuer="https://idp", roles=frozenset({"clerk"}))


def client(required: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    actor = bearer_actor(FakeAuthenticator(), required=required)

    @app.get("/me")
    async def me(principal: Principal | None = Depends(actor)):
        return {"id": principal.id if principal else None}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_bearer_actor():
    api = client()
    ok = await api.get("/me", headers={"Authorization": "Bearer good"})
    assert ok.json() == {"id": "oidc:u1"}
    missing = await api.get("/me")
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    bad = await api.get("/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "RE-E701"
    assert 'error="invalid_token"' in bad.headers["www-authenticate"]


async def test_optional_bearer_actor():
    api = client(required=False)
    assert (await api.get("/me")).json() == {"id": None}
    assert (await api.get("/me", headers={"Authorization": "Bearer nope"})).status_code == 401
