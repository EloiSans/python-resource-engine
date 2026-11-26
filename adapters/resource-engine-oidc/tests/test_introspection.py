"""Opaque tokens: introspection (RFC 7662) against a fake endpoint and Keycloak."""

from __future__ import annotations

import time

import httpx
import pytest
from resource_engine.errors import AuthenticationFailed, RemoteCallFailed
from resource_engine_oidc import (
    IntrospectionAuthenticator,
    TokenRouter,
    keycloak_introspection,
    okta_introspection,
)

ENDPOINT = "https://sso.example.com/oauth2/default/v1/introspect"


class Provider:
    """A fake introspection endpoint: tokens it knows, and the calls it got."""

    def __init__(self) -> None:
        now = int(time.time())
        self.tokens = {
            "opaque-ada": {
                "active": True,
                "sub": "ada",
                "client_id": "web",
                "scope": "invoices:read invoices:write",
                "aud": "invoices-api",
                "iss": "https://sso.example.com/oauth2/default",
                "exp": now + 3600,
                "groups": ["accountants"],
            },
            "opaque-robot": {
                "active": True,
                "client_id": "erp-sync",
                "aud": ["invoices-api"],
                "exp": now + 3600,
            },
            "opaque-other-api": {"active": True, "sub": "x", "aud": "billing", "exp": now + 60},
        }
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.headers.get("authorization") != httpx.BasicAuth("api", "s3cret")._auth_header:
            return httpx.Response(401)
        token = dict(httpx.QueryParams(request.content.decode()))["token"]
        return httpx.Response(200, json=self.tokens.get(token, {"active": False}))


@pytest.fixture
def provider():
    return Provider()


def _auth(provider, **kw):
    return okta_introspection(
        "sso.example.com",
        client_id=kw.pop("client_id", "api"),
        client_secret="s3cret",
        audience="invoices-api",
        http=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
        **kw,
    )


async def test_active_tokens_become_principals_and_are_cached(provider):
    auth = _auth(provider)
    ada = await auth.authenticate("opaque-ada")
    assert (ada.subject, ada.provider, ada.client_id) == ("ada", "okta", "web")
    assert ada.scopes == {"invoices:read", "invoices:write"} and ada.groups == {"accountants"}
    assert not ada.service
    await auth.authenticate("opaque-ada")
    assert len(provider.calls) == 1  # the second answer came from the cache
    robot = await auth.authenticate("opaque-robot")  # client credentials: no sub
    assert (robot.subject, robot.service) == ("erp-sync", True)


async def test_revocation_is_seen_once_the_cache_expires(provider):
    auth = _auth(provider, cache_ttl=0)  # no cache: every request asks
    await auth.authenticate("opaque-ada")
    provider.tokens["opaque-ada"]["active"] = False  # logged out / revoked
    with pytest.raises(AuthenticationFailed) as caught:
        await auth.authenticate("opaque-ada")
    assert caught.value.reason == "inactive"


@pytest.mark.parametrize(
    ("token", "reason"),
    [("unknown", "inactive"), ("opaque-other-api", "invalid_audience")],
)
async def test_refused_tokens(provider, token, reason):
    with pytest.raises(AuthenticationFailed) as caught:
        await _auth(provider).authenticate(token)
    assert caught.value.reason == reason


async def test_expired_cached_answers_and_provider_failures(provider):
    auth = _auth(provider)
    await auth.authenticate("opaque-ada")
    auth.clock = lambda: time.time() + 7200  # past exp, even if the cache still had it
    with pytest.raises(AuthenticationFailed, match="expired"):
        await auth.authenticate("opaque-ada")
    with pytest.raises(RemoteCallFailed, match="client credentials"):
        await _auth(provider, client_id="wrong").authenticate("opaque-ada")

    def down(request):
        raise httpx.ConnectError("unreachable")

    broken = IntrospectionAuthenticator(
        ENDPOINT,
        client_id="api",
        client_secret="s3cret",
        http=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(RemoteCallFailed):
        await broken.authenticate("opaque-ada")


async def test_the_router_sends_jwts_to_signature_checks(provider):
    class Jwt:
        async def authenticate(self, token):
            return "jwt"

    router = TokenRouter(jwt=Jwt(), opaque=_auth(provider))
    assert await router.authenticate("eyJhbGciOiJSUzI1NiJ9.e30.sig") == "jwt"
    assert (await router.authenticate("opaque-ada")).subject == "ada"
    with pytest.raises(AuthenticationFailed, match="not active"):  # dots, but no JWT header
        await router.authenticate("not.a.jwt")


KEYCLOAK = "http://127.0.0.1:8180"


@pytest.mark.emulator("keycloak")
async def test_keycloak_introspection():
    token_url = f"{KEYCLOAK}/realms/invoices/protocol/openid-connect/token"
    async with httpx.AsyncClient() as http:
        issued = await http.post(
            token_url,
            data={
                "grant_type": "password",
                "client_id": "invoices-dev-tests",
                "client_secret": "invoices-dev-secret",
                "username": "eli",
                "password": "dev",
                "scope": "openid organization",
            },
        )
        issued.raise_for_status()
        tokens = issued.json()
        auth = keycloak_introspection(
            KEYCLOAK,
            "invoices",
            client_id="invoices-dev-tests",
            client_secret="invoices-dev-secret",
            tenant_claim="organization",
            cache_ttl=0,
            http=http,
        )
        eli = await auth.authenticate(tokens["access_token"])
        assert (eli.provider, eli.tenant) == ("keycloak", "acme")
        assert {"issuer"} <= eli.roles
        # logging out revokes the session: introspection sees it at once
        logout = await http.post(
            f"{KEYCLOAK}/realms/invoices/protocol/openid-connect/logout",
            data={
                "client_id": "invoices-dev-tests",
                "client_secret": "invoices-dev-secret",
                "refresh_token": tokens["refresh_token"],
            },
        )
        assert logout.status_code == 204
        with pytest.raises(AuthenticationFailed, match="not active"):
            await auth.authenticate(tokens["access_token"])
