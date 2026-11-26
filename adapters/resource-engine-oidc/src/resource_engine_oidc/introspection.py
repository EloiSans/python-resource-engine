"""Opaque access tokens: ask the provider about each token (OAuth 2.0 token
introspection, RFC 7662) instead of verifying a signature.

For providers or clients that issue opaque (non-JWT) access tokens, and for
immediate revocation: a revoked or logged-out token stops working as soon as
the cached answer expires (``cache_ttl``, 30 s by default), instead of at its
expiry.

```python
from resource_engine_oidc import keycloak_introspection

tokens = keycloak_introspection(
    "https://sso.example.com", "acme",
    client_id="invoices-api", client_secret=os.environ["INTROSPECTION_SECRET"],
    audience="invoices-api",
)
mount_resources(app, registry, manager, actor_dependency=bearer_actor(tokens))
```
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import jwt
from resource_engine.calls import outgoing_call
from resource_engine.errors import AuthenticationFailed, RemoteCallFailed
from resource_engine.ports.identity import Authenticator, Principal

from resource_engine_oidc.verifier import Claims, ClaimsCheck, ClaimsMapper, split


def _fail(reason: str, message: str) -> AuthenticationFailed:
    return AuthenticationFailed(message, reason=reason)


def introspection_principal(provider: str, issuer: str | None) -> ClaimsMapper:
    """Introspection claims → ``Principal``. Client credentials tokens have no
    ``sub`` at some providers: the client id is then the subject."""

    def mapper(claims: Claims) -> Principal:
        subject = claims.get("sub") or claims.get("client_id")
        return Principal(
            subject=str(subject),
            issuer=str(claims.get("iss") or issuer or ""),
            provider=provider,
            name=claims.get("name") or claims.get("username") or claims.get("preferred_username"),
            email=claims.get("email"),
            roles=split(claims.get("roles")),
            groups=split(claims.get("groups")),
            scopes=split(claims.get("scope") or claims.get("scp")),
            client_id=claims.get("client_id") or claims.get("azp"),
            service=claims.get("sub") is None,
            claims=claims,
        )

    return mapper


class IntrospectionAuthenticator:
    """``Authenticator`` that checks tokens at the provider's introspection
    endpoint (RFC 7662), with a short cache of the answers.

    Args:
        endpoint: The introspection URL.
        client_id / client_secret: The API's own client, allowed to introspect
            (sent as HTTP Basic, ``client_secret_basic``).
        audience: Accepted ``aud`` value(s); ``None`` skips the check.
        issuer: Expected ``iss`` when the answer carries one; also the
            principal's issuer when it doesn't.
        provider / to_principal / check: As for ``OidcAuthenticator``.
        cache: A ``Cache`` (``InMemoryCache``, ``RedisCache``) for active
            answers, by token hash, for at most ``cache_ttl`` seconds and never
            past the token's ``exp``. ``None`` asks the provider every time.
        cache_ttl: How long a revoked token may still be accepted.
        http: ``httpx.AsyncClient`` (owned by the caller).
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client_id: str,
        client_secret: str,
        audience: str | Sequence[str] | None = None,
        issuer: str | None = None,
        provider: str = "oauth",
        to_principal: ClaimsMapper | None = None,
        check: ClaimsCheck | None = None,
        cache: Any = None,
        cache_ttl: float = 30.0,
        leeway: float = 30.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.auth = (client_id, client_secret)
        self.audience = {audience} if isinstance(audience, str) else set(audience or ())
        self.issuer = issuer
        self.provider = provider
        self.to_principal = to_principal or introspection_principal(provider, issuer)
        self.check = check
        if cache is None:
            from resource_engine.cache import InMemoryCache

            cache = InMemoryCache(max_entries=50_000)
        self.cache = cache if cache_ttl > 0 else None
        self.cache_ttl = cache_ttl
        self.leeway = leeway
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self.clock: Callable[[], float] = time.time

    async def authenticate(self, token: str) -> Principal:
        key = "re:introspect:" + hashlib.sha256(token.encode()).hexdigest()
        claims = await self._cached(key)
        if claims is None:
            claims = await self._introspect(token)
            self._validate(claims)
            await self._remember(key, claims)
        else:
            self._validate(claims)  # still within exp and nbf
        if self.check is not None:
            self.check(claims)
        return self.to_principal(claims)

    async def _introspect(self, token: str) -> Claims:
        async with outgoing_call(
            self.provider, "introspect", method="POST", url=self.endpoint
        ) as call:
            try:
                response = await self.http.post(
                    self.endpoint,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=self.auth,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise RemoteCallFailed(
                    f"Token introspection failed: {type(exc).__name__}",
                    service=self.provider,
                    operation="introspect",
                ) from exc
            call.status = response.status_code
        if response.status_code in (401, 403):
            raise RemoteCallFailed(
                "The introspection endpoint refused the API's client credentials",
                service=self.provider,
                operation="introspect",
                status=response.status_code,
            )
        if response.status_code != 200:
            raise RemoteCallFailed(
                f"Token introspection returned HTTP {response.status_code}",
                service=self.provider,
                operation="introspect",
                status=response.status_code,
            )
        claims = response.json()
        if not isinstance(claims, dict) or claims.get("active") is not True:
            raise _fail("inactive", "The token is not active (expired, revoked or unknown)")
        return claims

    def _validate(self, claims: Claims) -> None:
        now = self.clock()
        exp = claims.get("exp")
        if isinstance(exp, int | float) and exp + self.leeway < now:
            raise _fail("expired", "The token has expired")
        nbf = claims.get("nbf")
        if isinstance(nbf, int | float) and nbf - self.leeway > now:
            raise _fail("not_yet_valid", "The token is not valid yet")
        if self.issuer and claims.get("iss") and claims["iss"] != self.issuer:
            raise _fail("invalid_issuer", "The token comes from an untrusted issuer")
        if self.audience:
            aud = claims.get("aud")
            given = {aud} if isinstance(aud, str) else set(aud or ())
            if not given & self.audience:
                raise _fail("invalid_audience", "The token was issued for another audience")
        if not (claims.get("sub") or claims.get("client_id")):
            raise _fail("invalid_token", "The introspection answer names no subject")

    async def _cached(self, key: str) -> Claims | None:
        if self.cache is None:
            return None
        try:
            return await self.cache.get(key)
        except Exception:
            return None

    async def _remember(self, key: str, claims: Claims) -> None:
        if self.cache is None:
            return
        ttl = self.cache_ttl
        exp = claims.get("exp")
        if isinstance(exp, int | float):
            ttl = min(ttl, exp - self.clock())
        if ttl > 0:
            with contextlib.suppress(Exception):  # an unavailable cache only costs a call
                await self.cache.set(key, claims, ttl)


class TokenRouter:
    """JWTs to ``jwt`` (signature checks, no call), anything else to ``opaque``
    (introspection): for providers that issue both."""

    def __init__(self, *, jwt: Authenticator, opaque: Authenticator) -> None:
        self.jwt = jwt
        self.opaque = opaque

    async def authenticate(self, token: str) -> Principal:
        if token.count(".") == 2:
            try:
                header = jwt_header(token)
            except Exception:
                header = None
            if isinstance(header, dict) and header.get("alg"):
                return await self.jwt.authenticate(token)
        return await self.opaque.authenticate(token)


def jwt_header(token: str) -> Any:
    return jwt.get_unverified_header(token)


def keycloak_introspection(
    base_url: str,
    realm: str,
    *,
    client_id: str,
    client_secret: str,
    audience: str | Sequence[str] | None = None,
    tenant_claim: str | None = None,
    cache: Any = None,
    cache_ttl: float = 30.0,
    http: httpx.AsyncClient | None = None,
) -> IntrospectionAuthenticator:
    """Keycloak realm: introspection with a confidential client, the same roles
    and tenant mapping as the ``keycloak`` preset. Works for Keycloak's JWTs
    too, when revocation must take effect at once (logout, disabled user)."""
    from resource_engine_oidc.providers import keycloak_principal

    issuer = f"{base_url.rstrip('/')}/realms/{realm}"
    return IntrospectionAuthenticator(
        f"{issuer}/protocol/openid-connect/token/introspect",
        client_id=client_id,
        client_secret=client_secret,
        audience=audience,
        issuer=issuer,
        provider="keycloak",
        to_principal=keycloak_principal(realm, tenant_claim=tenant_claim),
        cache=cache,
        cache_ttl=cache_ttl,
        http=http,
    )


def okta_introspection(
    domain: str,
    *,
    client_id: str,
    client_secret: str,
    authorization_server: str = "default",
    audience: str | Sequence[str] | None = None,
    cache: Any = None,
    cache_ttl: float = 30.0,
    http: httpx.AsyncClient | None = None,
) -> IntrospectionAuthenticator:
    """Okta custom authorization server (``default`` unless named): groups from
    the ``groups`` claim when the server adds it, scopes from ``scope``."""
    issuer = f"https://{domain}/oauth2/{authorization_server}"
    return IntrospectionAuthenticator(
        f"{issuer}/v1/introspect",
        client_id=client_id,
        client_secret=client_secret,
        audience=audience,
        issuer=issuer,
        provider="okta",
        cache=cache,
        cache_ttl=cache_ttl,
        http=http,
    )


__all__ = [
    "IntrospectionAuthenticator",
    "TokenRouter",
    "introspection_principal",
    "keycloak_introspection",
    "okta_introspection",
]
