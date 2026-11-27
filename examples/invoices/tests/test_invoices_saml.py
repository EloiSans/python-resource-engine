"""SAML sign-in through Keycloak as an identity broker.

The ``partner`` realm (docker/keycloak/partner-realm.json) plays a partner
company's SAML identity provider. The ``invoices`` realm federates it as
``partner-saml``: users sign in at the partner, Keycloak checks the signed
assertion, maps its ``Role`` attribute to realm roles and the user to the
Initech organization, and issues the usual OIDC tokens. The app never sees
SAML: it verifies the same JWTs as for every other user.

The tests drive the browser flow with httpx: authorization code with PKCE,
the SAML request and response forms, the partner's login form.

Needs ``docker compose -f compose.emulators.yaml up -d keycloak``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import urllib.request
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from invoices.app import create_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

KEYCLOAK = "http://127.0.0.1:8180"
REALM = f"{KEYCLOAK}/realms/invoices"
REDIRECT = "http://localhost:5173/callback"


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"{KEYCLOAK}/realms/partner", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = [pytest.mark.emulator("keycloak")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason=f"Keycloak partner realm not at {KEYCLOAK}"))


class LocalhostTransport(httpx.HTTPTransport):
    """Browsers send ``Secure`` cookies to 127.0.0.1 over http; httpx doesn't,
    so the flag is dropped from Keycloak's cookies."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        items = response.headers.multi_items()
        if any(k.lower() == "set-cookie" for k, _ in items):
            response.headers = httpx.Headers(
                [
                    (k, ";".join(p for p in v.split(";") if p.strip().lower() != "secure"))
                    if k.lower() == "set-cookie"
                    else (k, v)
                    for k, v in items
                ]
            )
        return response


class _Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms.append({"action": html.unescape(a.get("action") or ""), "fields": {}})
        elif tag == "input" and self.forms and a.get("name"):
            self.forms[-1]["fields"][a["name"]] = a.get("value") or ""


def saml_sign_in(username: str, *, tamper=None) -> dict | None:
    """Sign in at the partner as a browser would; the token response, or
    ``None`` when the broker refuses. ``tamper`` may rewrite the SAML response."""
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    query = {
        "client_id": "invoices-web",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid organization",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "kc_idp_hint": "partner-saml",  # straight to the partner, no Keycloak login page
    }
    with httpx.Client(transport=LocalhostTransport()) as browser:
        response = browser.get(f"{REALM}/protocol/openid-connect/auth?" + urlencode(query))
        for _ in range(10):
            while response.is_redirect:
                location = urljoin(str(response.url), response.headers["location"])
                if location.startswith(REDIRECT):
                    code = parse_qs(urlsplit(location).query)["code"][0]
                    tokens = httpx.post(
                        f"{REALM}/protocol/openid-connect/token",
                        data={
                            "grant_type": "authorization_code",
                            "client_id": "invoices-web",
                            "code": code,
                            "redirect_uri": REDIRECT,
                            "code_verifier": verifier,
                        },
                    )
                    tokens.raise_for_status()
                    return tokens.json()
                response = browser.get(location)
            parser = _Forms()
            parser.feed(response.text)
            if not parser.forms:
                return None  # an error page
            form = parser.forms[0]
            fields = form["fields"]
            if "password" in fields:  # the partner's login form
                fields.update(username=username, password="dev")
            if "SAMLResponse" in fields and tamper is not None:
                fields["SAMLResponse"] = tamper(fields["SAMLResponse"])
            response = browser.post(urljoin(str(response.url), form["action"]), data=fields)
    raise AssertionError("the sign-in did not finish")


def bearer(username: str) -> dict[str, str]:
    tokens = saml_sign_in(username)
    assert tokens is not None
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def password_bearer(username: str) -> dict[str, str]:
    """A local user of the invoices realm (development-only client)."""
    response = httpx.post(
        f"{REALM}/protocol/openid-connect/token",
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
    with TestClient(create_app(engine)) as c:
        yield c


def test_partner_users_sign_in_with_saml_and_work_in_their_organization(client):
    sara, tom = bearer("sara"), bearer("tom")  # approver and accountant at Initech
    me = client.get("/authz/me", headers=sara).json()
    assert me["principal"].startswith("keycloak:") and me["roles"] == ["approver"]

    root = password_bearer("root")
    created = client.post(
        "/invoices",
        params={"tenant": "initech"},
        json={"customer": "Initech", "amount": "120"},
        headers=root,
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    client.post(f"/invoices/{invoice['id']}/transitions/submit", headers=root).raise_for_status()

    assert client.get("/invoices", headers=tom).json()["total"] == 1
    assert client.get("/invoices", headers=password_bearer("eli")).json()["total"] == 0  # ACME
    denied = client.post("/invoices", json={"customer": "x", "amount": "1"}, headers=tom)
    assert denied.json()["error"]["code"] == "RE-E702"
    rejected = client.post(
        f"/invoices/{invoice['id']}/transitions/reject",
        json={"reason": "Sense comanda"},
        headers=sara,
    )
    assert rejected.json()["status"] == "rejected", rejected.text


def test_a_tampered_assertion_is_refused():
    def promote(saml_response: str) -> str:
        xml = base64.b64decode(saml_response).decode()
        assert ">approver<" in xml
        return base64.b64encode(xml.replace(">approver<", ">invoices-admin<").encode()).decode()

    assert saml_sign_in("sara", tamper=promote) is None
