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


class RecordingAuthenticator:
    """A request authenticator that keeps what it was given."""

    def __init__(self) -> None:
        self.seen = []

    async def authenticate(self, token: str) -> Principal:
        raise AssertionError("authenticate_request is preferred")

    async def authenticate_request(self, credentials):
        self.seen.append(credentials)
        if credentials.proof == "bad":
            raise AuthenticationFailed("bad proof", reason="invalid_dpop_proof")
        return Principal(subject="u1", issuer="https://idp")


def _proof_client(authenticator, **kw) -> httpx.AsyncClient:
    from resource_engine_fastapi import forwarded_client_certificate

    app = FastAPI()
    install_exception_handlers(app)
    actor = bearer_actor(authenticator, client_certificate=forwarded_client_certificate(), **kw)

    @app.post("/invoices/{id}")
    async def act(id: int, principal: Principal = Depends(actor)):
        return {"id": principal.id}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api:8000")


async def test_dpop_credentials_reach_a_request_authenticator():
    import base64
    from urllib.parse import quote

    der = b"0\x82certificate"
    pem = (
        "-----BEGIN CERTIFICATE-----\n"
        + base64.b64encode(der).decode()
        + "\n-----END CERTIFICATE-----\n"
    )
    authenticator = RecordingAuthenticator()
    api = _proof_client(authenticator)
    ok = await api.post(
        "/invoices/7?x=1",
        headers={"Authorization": "DPoP tok", "DPoP": "proof", "X-Client-Cert": quote(pem)},
    )
    assert ok.status_code == 200
    seen = authenticator.seen[-1]
    assert (seen.token, seen.scheme, seen.proof) == ("tok", "dpop", "proof")
    assert (seen.method, seen.url) == ("POST", "http://api:8000/invoices/7")
    assert seen.client_certificate == der

    bad = await api.post("/invoices/7", headers={"Authorization": "DPoP tok", "DPoP": "bad"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"].startswith('DPoP error="invalid_dpop_proof"')
    twice = await api.post(
        "/invoices/7",
        headers=[("Authorization", "DPoP tok"), ("DPoP", "a"), ("DPoP", "b")],
    )
    assert twice.status_code == 401

    behind_proxy = _proof_client(authenticator, public_url="https://api.example.com/")
    await behind_proxy.post("/invoices/7", headers={"Authorization": "Bearer tok"})
    assert authenticator.seen[-1].url == "https://api.example.com/invoices/7"


async def test_plain_authenticators_refuse_dpop_tokens():
    api = client()
    refused = await api.get("/me", headers={"Authorization": "DPoP good", "DPoP": "proof"})
    assert refused.status_code == 401
    assert refused.json()["error"]["details"]["reason"] == "unsupported_scheme"
