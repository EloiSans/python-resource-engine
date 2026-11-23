"""Generic OIDC access/ID token verification against the provider's JWKS."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
import jwt
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Principal

#: Asymmetric algorithms only: ``none`` and HMAC are never accepted.
DEFAULT_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")

type Claims = dict[str, Any]
type ClaimsMapper = Callable[[Claims], Principal]
type ClaimsCheck = Callable[[Claims], None]
type IssuerCheck = Callable[[str, Claims], bool]


def _fail(reason: str, message: str) -> AuthenticationFailed:
    return AuthenticationFailed(message, reason=reason)


def split(value: Any) -> frozenset[str]:
    """Claims that may be a space-separated string or a list."""
    if not value:
        return frozenset()
    if isinstance(value, str):
        return frozenset(value.split())
    return frozenset(str(v) for v in value)


def default_principal(provider: str) -> ClaimsMapper:
    def mapper(claims: Claims) -> Principal:
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            provider=provider,
            name=claims.get("name") or claims.get("preferred_username"),
            email=claims.get("email"),
            roles=split(claims.get("roles")),
            groups=split(claims.get("groups")),
            scopes=split(claims.get("scope") or claims.get("scp")),
            client_id=claims.get("azp") or claims.get("client_id"),
            claims=claims,
        )

    return mapper


class JwksCache:
    """Signing keys by ``kid``, refreshed on TTL expiry or on an unknown ``kid``
    (key rotation), at most once per ``min_refresh`` seconds."""

    def __init__(
        self,
        jwks_uri: str | Callable[[], Any],
        http: httpx.AsyncClient,
        *,
        ttl: float = 3600.0,
        min_refresh: float = 30.0,
    ) -> None:
        self._uri = jwks_uri
        self._http = http
        self.ttl = ttl
        self.min_refresh = min_refresh
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched = 0.0
        self._lock = asyncio.Lock()

    async def _uri_value(self) -> str:
        uri = self._uri
        return await uri() if callable(uri) else uri

    async def _refresh(self) -> None:
        try:
            response = await self._http.get(await self._uri_value())
            response.raise_for_status()
            keys = {}
            for data in response.json().get("keys", []):
                if data.get("use", "sig") != "sig" or "kid" not in data:
                    continue
                try:
                    keys[data["kid"]] = jwt.PyJWK(data)
                except jwt.PyJWKError:
                    continue  # unsupported key type: skip it
        except (httpx.HTTPError, ValueError) as exc:
            raise _fail("jwks_unavailable", f"Signing keys could not be loaded: {exc}") from exc
        self._keys, self._fetched = keys, time.monotonic()

    async def key(self, kid: str | None) -> jwt.PyJWK:
        async with self._lock:
            now = time.monotonic()
            stale = now - self._fetched > self.ttl
            unknown = kid not in self._keys and now - self._fetched > self.min_refresh
            if not self._keys or stale or unknown:
                await self._refresh()
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        try:
            return self._keys[kid]  # type: ignore[index]
        except KeyError:
            raise _fail("unknown_key", "The token is signed with an unknown key") from None


class OidcAuthenticator:
    """``Authenticator`` for JWTs issued by an OpenID Connect provider.

    Args:
        issuer: Expected ``iss``; or a function ``(iss, claims) -> bool`` for
            multi-tenant providers (then pass ``jwks_uri``).
        audience: Accepted ``aud`` value(s); ``None`` skips the check (only for
            providers that do not set it, such as Cognito access tokens, and then
            ``check`` must validate the client).
        jwks_uri: Signing keys URL; discovered from ``issuer`` when omitted.
        provider: Name stored in ``Principal.provider``.
        to_principal: Claims → ``Principal`` (presets map provider-specific claims).
        check: Extra validation; raise ``AuthenticationFailed`` to refuse.
        leeway: Clock skew tolerance, in seconds.
        http: ``httpx.AsyncClient`` for discovery and JWKS (owned by the caller).
    """

    def __init__(
        self,
        *,
        issuer: str | IssuerCheck,
        audience: str | Sequence[str] | None,
        jwks_uri: str | None = None,
        provider: str = "oidc",
        to_principal: ClaimsMapper | None = None,
        check: ClaimsCheck | None = None,
        algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
        leeway: float = 30.0,
        required_claims: Sequence[str] = ("exp", "iat", "sub", "iss"),
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if jwks_uri is None and not isinstance(issuer, str):
            raise ValueError("A callable issuer needs an explicit jwks_uri")
        self.issuer = issuer
        self.audience = [audience] if isinstance(audience, str) else audience
        self.provider = provider
        self.to_principal = to_principal or default_principal(provider)
        self.check = check
        self.algorithms = [a for a in algorithms if not a.startswith("HS") and a != "none"]
        self.leeway = leeway
        self.required_claims = list(required_claims)
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self.jwks = JwksCache(jwks_uri or self._discover, self.http)

    async def _discover(self) -> str:
        assert isinstance(self.issuer, str)
        url = f"{self.issuer.rstrip('/')}/.well-known/openid-configuration"
        response = await self.http.get(url)
        response.raise_for_status()
        return response.json()["jwks_uri"]

    async def authenticate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.DecodeError:
            raise _fail("invalid_token", "The bearer token is not a JWT") from None
        algorithm = header.get("alg")
        if algorithm not in self.algorithms:
            raise _fail("invalid_algorithm", f"Signing algorithm {algorithm!r} is not accepted")
        key = await self.jwks.key(header.get("kid"))
        try:
            claims: Claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer if isinstance(self.issuer, str) else None,
                leeway=self.leeway,
                options={
                    "require": self.required_claims,
                    "verify_aud": self.audience is not None,
                },
            )
        except jwt.ExpiredSignatureError:
            raise _fail("expired", "The token has expired") from None
        except jwt.ImmatureSignatureError:
            raise _fail("not_yet_valid", "The token is not valid yet") from None
        except jwt.InvalidAudienceError:
            raise _fail("invalid_audience", "The token was issued for another audience") from None
        except jwt.InvalidIssuerError:
            raise _fail("invalid_issuer", "The token comes from an untrusted issuer") from None
        except jwt.InvalidSignatureError:
            raise _fail("invalid_signature", "The token signature is not valid") from None
        except jwt.MissingRequiredClaimError as exc:
            raise _fail("invalid_token", f"The token lacks the {exc.claim!r} claim") from None
        except jwt.InvalidTokenError as exc:
            raise _fail("invalid_token", f"The token is not valid: {type(exc).__name__}") from None
        if not isinstance(self.issuer, str) and not self.issuer(str(claims.get("iss")), claims):
            raise _fail("invalid_issuer", "The token comes from an untrusted issuer")
        if self.check is not None:
            self.check(claims)
        return self.to_principal(claims)


def claim_path(claims: Mapping[str, Any], path: str) -> Any:
    """``"realm_access.roles"`` → nested value (``None`` when missing)."""
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value
