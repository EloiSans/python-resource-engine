"""Keycloak-shaped tokens signed with a local key, for tests and demos without Keycloak.

The app verifies them exactly as real ones (signature, issuer, audience,
expiry) through the Keycloak preset; only the JWKS comes from memory.
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from resource_engine_oidc import keycloak

BASE_URL = "https://sso.invoices.test"
REALM = "invoices"
ISSUER = f"{BASE_URL}/realms/{REALM}"


class Tokens:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex[:8]

    def _jwks(self, request: httpx.Request) -> httpx.Response:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return httpx.Response(200, json={"keys": [{**jwk, "kid": self.kid, "alg": "RS256"}]})

    def authenticator(self):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self._jwks))
        return keycloak(
            BASE_URL, REALM, audience="invoices-api", tenant_claim="organization", http=http
        )

    def token(self, username: str, roles: list[str], organization: str | None = None) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "invoices-api",
            "sub": f"user-{username}",
            "azp": "invoices-web",
            "preferred_username": username,
            "iat": now,
            "exp": now + 300,
            "realm_access": {"roles": roles},
        }
        if organization:
            claims["organization"] = [organization]
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def headers(self, username: str, roles: list[str], organization: str | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token(username, roles, organization)}"}
