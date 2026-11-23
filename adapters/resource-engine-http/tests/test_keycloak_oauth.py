"""Outbound OAuth 2.0 client credentials against a real Keycloak."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from resource_engine_http import OAuth2ClientCredentials

KEYCLOAK = "http://127.0.0.1:8180"
TOKEN_URL = f"{KEYCLOAK}/realms/resource-engine/protocol/openid-connect/token"
CLIENT_ID, CLIENT_SECRET = "resource-engine-service", "resource-engine-dev-secret"

pytestmark = pytest.mark.emulator("keycloak")


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


async def test_tokens_from_keycloak_are_cached_and_refreshed_on_401():
    seen: list[str] = []
    reject = {"next": False}

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        if reject["next"]:
            reject["next"] = False
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    auth = OAuth2ClientCredentials(TOKEN_URL, CLIENT_ID, CLIENT_SECRET)
    async with httpx.AsyncClient(
        auth=auth,
        mounts={KEYCLOAK: httpx.AsyncHTTPTransport(), "http://api.test": httpx.MockTransport(api)},
    ) as client:
        assert (await client.get("http://api.test/a")).status_code == 200
        assert (await client.get("http://api.test/b")).status_code == 200
        reject["next"] = True
        assert (await client.get("http://api.test/c")).status_code == 200

    first, second, rejected, refreshed = seen
    assert first == second == rejected and refreshed != first  # cached, then renewed
    token = claims(refreshed)
    assert token["azp"] == CLIENT_ID and "resource-engine-api" in token["aud"]
    assert "accountant" in token["realm_access"]["roles"]
