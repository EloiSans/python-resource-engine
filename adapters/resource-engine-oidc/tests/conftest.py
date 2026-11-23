"""Locally signed tokens with a fake JWKS endpoint, plus the real Keycloak check."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

KEYCLOAK = "http://127.0.0.1:8180"


def _keycloak_up() -> bool:
    try:
        url = f"{KEYCLOAK}/realms/resource-engine/.well-known/openid-configuration"
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("keycloak",)
    ]
    if not selected or _keycloak_up():
        return
    skip = pytest.mark.skip(reason=f"Keycloak not reachable at {KEYCLOAK}")
    for item in selected:
        item.add_marker(skip)


class Signer:
    """An RSA key pair (rotatable) published at every JWKS URL the presets use."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.jwks_calls = 0
        self.rotate()

    def rotate(self) -> str:
        kid = uuid.uuid4().hex[:8]
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = kid
        return kid

    def jwks(self) -> dict:
        keys = []
        for kid, key in self.keys.items():
            data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            keys.append({**data, "kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": keys}

    def token(self, claims: dict, *, kid: str | None = None, ttl: int = 300, **headers) -> str:
        now = int(time.time())
        payload = {"iat": now, "exp": now + ttl, **claims}
        kid = kid or self.kid
        return jwt.encode(
            payload, self.keys[kid], algorithm="RS256", headers={"kid": kid, **headers}
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.jwks_calls += 1
            return httpx.Response(200, json=self.jwks())

        return httpx.MockTransport(handler)


@pytest.fixture
def signer():
    return Signer()


@pytest.fixture
def http(signer):
    return httpx.AsyncClient(transport=signer.transport())
