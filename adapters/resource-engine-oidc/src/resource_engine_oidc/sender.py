"""Sender-constrained access tokens: a stolen token is useless without the
client's key.

- **DPoP** (RFC 9449): the token carries ``cnf.jkt``, the thumbprint of a key
  the client holds; every request sends ``Authorization: DPoP <token>`` and a
  ``DPoP`` header, a fresh JWT signed with that key naming the method, the URL
  and a hash of the token.
- **mTLS** (RFC 8705): the token carries ``cnf["x5t#S256"]``, the thumbprint of
  the client's TLS certificate, and the request must come over that
  certificate.

```python
from resource_engine_oidc import SenderConstrained, keycloak

tokens = SenderConstrained(keycloak("https://sso.example.com", "acme", audience="api"))
mount_resources(app, registry, manager, actor_dependency=bearer_actor(tokens))
```

Bound tokens are always checked; ``required=True`` also refuses tokens that
are not bound at all. The wrapped authenticator verifies the token itself
(signature or introspection); both put ``cnf`` in ``Principal.claims``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt
from resource_engine.cache import InMemoryCache
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Authenticator, Credentials, Principal

#: Asymmetric algorithms accepted for DPoP proofs.
DPOP_ALGORITHMS = ("ES256", "ES384", "ES512", "RS256", "PS256", "PS384", "PS512", "EdDSA")

#: The JWK members a thumbprint covers (RFC 7638), by key type.
THUMBPRINT_MEMBERS = {
    "RSA": ("e", "kty", "n"),
    "EC": ("crv", "kty", "x", "y"),
    "OKP": ("crv", "kty", "x"),
}

PRIVATE_MEMBERS = ("d", "p", "q", "dp", "dq", "qi", "oth", "k")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _proof_error(message: str) -> AuthenticationFailed:
    return AuthenticationFailed(message, reason="invalid_dpop_proof")


def _token_error(message: str) -> AuthenticationFailed:
    return AuthenticationFailed(message, reason="invalid_token")


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    """The RFC 7638 SHA-256 thumbprint of a public JWK (what ``cnf.jkt`` holds)."""
    members = THUMBPRINT_MEMBERS.get(str(jwk.get("kty")))
    if members is None or any(m not in jwk for m in members):
        raise _proof_error("The DPoP key is not a supported public key")
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return _b64(hashlib.sha256(canonical.encode()).digest())


def certificate_thumbprint(der: bytes) -> str:
    """The RFC 8705 ``x5t#S256`` of a DER certificate."""
    return _b64(hashlib.sha256(der).digest())


def access_token_hash(token: str) -> str:
    """The ``ath`` a DPoP proof carries for ``token``."""
    return _b64(hashlib.sha256(token.encode("ascii")).digest())


def normalise_url(url: str) -> str:
    """Scheme and host in lower case, default ports dropped, no query or fragment."""
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parts.path or "/", "", ""))


class SenderConstrained:
    """A ``RequestAuthenticator`` that checks the proof of possession of
    DPoP- and certificate-bound tokens, around any ``Authenticator``.

    Args:
        authenticator: Verifies the token (``OidcAuthenticator``, a preset,
            ``IntrospectionAuthenticator``, ``TokenRouter``...).
        dpop: Accept DPoP-bound tokens (``Authorization: DPoP``).
        mtls: Accept certificate-bound tokens. The entry adapter must pass the
            client certificate (``bearer_actor(client_certificate=...)``).
        required: Refuse tokens that are not bound to a key or certificate.
        replay_cache: Where proof ids (``jti``) are remembered, to refuse a
            proof sent twice. ``InMemoryCache`` by default; with several
            instances pass a shared one (``RedisCache``), whose ``add`` is atomic.
        max_age: How old a proof (``iat``) may be, in seconds.
        leeway: Clock skew tolerance, in seconds.
        algorithms: Accepted proof algorithms (asymmetric only).
    """

    def __init__(
        self,
        authenticator: Authenticator,
        *,
        dpop: bool = True,
        mtls: bool = False,
        required: bool = False,
        replay_cache: Any = None,
        max_age: float = 60.0,
        leeway: float = 5.0,
        algorithms: Sequence[str] = DPOP_ALGORITHMS,
    ) -> None:
        if not (dpop or mtls):
            raise ValueError("Enable dpop, mtls or both")
        self.authenticator = authenticator
        self.dpop = dpop
        self.mtls = mtls
        self.required = required
        self.replay_cache = replay_cache if replay_cache is not None else InMemoryCache()
        self.max_age = max_age
        self.leeway = leeway
        self.algorithms = tuple(a for a in algorithms if not a.startswith("HS") and a != "none")

    async def authenticate(self, token: str) -> Principal:
        """A bearer token without request details: only unbound tokens pass."""
        return await self.authenticate_request(Credentials(token=token))

    async def authenticate_request(self, credentials: Credentials) -> Principal:
        if credentials.scheme == "dpop" and not self.dpop:
            raise AuthenticationFailed("DPoP tokens are not accepted", reason="unsupported_scheme")
        principal = await self.authenticator.authenticate(credentials.token)
        cnf = principal.claims.get("cnf")
        cnf = cnf if isinstance(cnf, dict) else {}
        jkt, x5t = cnf.get("jkt"), cnf.get("x5t#S256")
        if credentials.scheme == "dpop" and not jkt:
            raise _token_error("The token is not bound to a DPoP key; send it as a bearer token")
        if jkt:
            if not self.dpop:
                raise _token_error("DPoP-bound tokens are not accepted")
            if credentials.scheme != "dpop":
                raise _token_error("A DPoP-bound token must be sent with 'Authorization: DPoP'")
            await self._check_proof(credentials, str(jkt))
        if x5t:
            if not self.mtls:
                raise _token_error("Certificate-bound tokens are not accepted")
            certificate = credentials.client_certificate
            if certificate is None:
                raise _token_error("The token is bound to a client certificate the request lacks")
            if not hmac.compare_digest(certificate_thumbprint(certificate), str(x5t)):
                raise _token_error("The token is bound to another client certificate")
        if self.required and not (jkt or x5t):
            raise _token_error("The token is not sender-constrained (DPoP or mTLS)")
        return principal

    async def _check_proof(self, credentials: Credentials, jkt: str) -> None:
        proof = credentials.proof
        if not proof:
            raise _proof_error("The DPoP header is missing")
        try:
            header = jwt.get_unverified_header(proof)
        except jwt.DecodeError:
            raise _proof_error("The DPoP proof is not a JWT") from None
        if header.get("typ") != "dpop+jwt":
            raise _proof_error("The DPoP proof must have typ 'dpop+jwt'")
        algorithm = header.get("alg")
        if algorithm not in self.algorithms:
            raise _proof_error(f"DPoP algorithm {algorithm!r} is not accepted")
        jwk = header.get("jwk")
        if not isinstance(jwk, dict) or any(m in jwk for m in PRIVATE_MEMBERS):
            raise _proof_error("The DPoP proof must carry the public key in 'jwk'")
        if not hmac.compare_digest(jwk_thumbprint(jwk), jkt):
            raise _proof_error("The DPoP key is not the one the token is bound to")
        try:
            key = jwt.PyJWK(jwk, algorithm=algorithm)
            claims = jwt.decode(
                proof,
                key,
                algorithms=[algorithm],
                options={
                    "require": ["iat", "jti", "htm", "htu"],
                    "verify_aud": False,
                    "verify_iat": False,
                },
            )
        except (jwt.InvalidTokenError, jwt.PyJWKError) as exc:
            raise _proof_error(f"The DPoP proof is not valid: {type(exc).__name__}") from None
        if credentials.method is None or credentials.url is None:
            raise _proof_error("The request's method and URL are needed to check DPoP")
        if str(claims["htm"]).upper() != credentials.method.upper():
            raise _proof_error("The DPoP proof was made for another method")
        if normalise_url(str(claims["htu"])) != normalise_url(credentials.url):
            raise _proof_error("The DPoP proof was made for another URL")
        now = time.time()
        iat = claims["iat"]
        if not isinstance(iat, int | float):
            raise _proof_error("The DPoP proof's iat is not a number")
        if iat > now + self.leeway or iat < now - self.max_age - self.leeway:
            raise _proof_error("The DPoP proof is too old or from the future")
        ath = claims.get("ath")
        if not isinstance(ath, str) or not hmac.compare_digest(
            ath, access_token_hash(credentials.token)
        ):
            raise _proof_error("The DPoP proof was made for another token")
        await self._remember(str(claims["jti"]), jkt)

    async def _remember(self, jti: str, jkt: str) -> None:
        key = "dpop:jti:" + hashlib.sha256(f"{jkt}:{jti}".encode()).hexdigest()
        ttl = self.max_age + 2 * self.leeway
        add = getattr(self.replay_cache, "add", None)
        if add is not None:
            fresh = await add(key, True, ttl)
        else:  # a plain Cache: not atomic across instances
            fresh = await self.replay_cache.get(key) is None
            if fresh:
                await self.replay_cache.set(key, True, ttl)
        if not fresh:
            raise _proof_error("The DPoP proof was already used")


__all__ = [
    "DPOP_ALGORITHMS",
    "SenderConstrained",
    "access_token_hash",
    "certificate_thumbprint",
    "jwk_thumbprint",
    "normalise_url",
]
