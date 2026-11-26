"""The example app with real Keycloak tokens (docker/keycloak/invoices-realm.json).

Needs ``docker compose -f compose.emulators.yaml up -d keycloak``.
"""

from __future__ import annotations

import urllib.request

import httpx
import pytest
from fastapi.testclient import TestClient
from invoices.app import create_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

KEYCLOAK = "http://127.0.0.1:8180"
TOKEN_URL = f"{KEYCLOAK}/realms/invoices/protocol/openid-connect/token"


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"{KEYCLOAK}/realms/invoices", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = [pytest.mark.emulator("keycloak")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason=f"Keycloak not reachable at {KEYCLOAK}"))


def bearer(username: str) -> dict[str, str]:
    """A user's access token from the example realm, through the development-only
    client (the front-end client only allows authorization code with PKCE)."""
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "invoices-dev-tests",
            "client_secret": "invoices-dev-secret",
            "username": username,
            "password": "dev",
            "scope": "openid organization",
        },
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with TestClient(create_app(engine)) as c:  # the app's own Keycloak authenticator
        yield c


def test_users_work_in_their_organization(client):
    eli, gina, ada, pau = (bearer(u) for u in ("eli", "gina", "ada", "pau"))
    created = client.post("/invoices", json={"customer": "ACME", "amount": "50"}, headers=eli)
    assert created.status_code == 201, created.text
    invoice = created.json()
    assert invoice["organization"] == "acme"
    client.post(
        "/invoices", json={"customer": "Globex", "amount": "9"}, headers=gina
    ).raise_for_status()

    assert client.get("/invoices", headers=ada).json()["total"] == 1  # accountant at ACME
    assert client.get(f"/invoices/{invoice['id']}", headers=gina).status_code == 404
    denied = client.post("/invoices", json={"customer": "x", "amount": "1"}, headers=ada)
    assert denied.json()["error"]["code"] == "RE-E702"

    client.post(f"/invoices/{invoice['id']}/transitions/submit", headers=eli).raise_for_status()
    rejected = client.post(
        f"/invoices/{invoice['id']}/transitions/reject",
        json={"reason": "Falta el NIF"},
        headers=pau,  # approver at ACME
    )
    assert rejected.json()["status"] == "rejected"


def test_the_front_end_client_has_no_password_grant():
    response = httpx.post(
        TOKEN_URL,
        data={"grant_type": "password", "client_id": "invoices-web", "username": "eli",
              "password": "dev"},
    )  # fmt: skip
    assert response.status_code in (400, 401)
    assert response.json()["error"] == "unauthorized_client"


def test_platform_admin_sees_every_organization(client):
    client.post(
        "/invoices", json={"customer": "A", "amount": "1"}, headers=bearer("eli")
    ).raise_for_status()
    client.post(
        "/invoices", json={"customer": "G", "amount": "1"}, headers=bearer("gina")
    ).raise_for_status()
    root = bearer("root")
    assert client.get("/invoices", headers=root).json()["total"] == 2
    page = client.get("/invoices", headers=root, params={"tenant": "globex"}).json()
    assert [i["organization"] for i in page["items"]] == ["globex"]
