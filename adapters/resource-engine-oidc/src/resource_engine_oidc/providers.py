"""Presets for common identity providers.

Each returns an ``OidcAuthenticator`` that knows the provider's issuer, key
location, token quirks and where it puts roles, groups and scopes.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Any

import httpx
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Principal

from resource_engine_oidc.verifier import Claims, OidcAuthenticator, claim_path, split

# ----------------------------------------------------------------- Keycloak

KEYCLOAK_BUILTIN_ROLES = ("offline_access", "uma_authorization")


def single_tenant(value: Any) -> str | None:
    """One tenant from a claim: a string, a one-item list, or a one-key map
    (Keycloak's organization claim with attributes); otherwise ``None``."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list | tuple) and len(value) == 1 and isinstance(value[0], str):
        return value[0]
    if isinstance(value, dict) and len(value) == 1:
        (key,) = value
        return str(key)
    return None


def keycloak(
    base_url: str,
    realm: str,
    *,
    audience: str | Sequence[str],
    client_id: str | None = None,
    tenant_claim: str | None = None,
    http: httpx.AsyncClient | None = None,
) -> OidcAuthenticator:
    """Keycloak realm.

    Roles are the realm roles plus the client roles of ``client_id`` (or of the
    token's ``azp``), without Keycloak's built-in ones. Add an *audience* mapper
    to the client so tokens carry ``audience``.

    ``tenant_claim`` fills ``Principal.tenant``:

    - ``"organization"`` for Keycloak Organizations (the ``organization`` client
      scope gives the user's organization aliases; a user in exactly one
      organization has it as tenant, in several none, so tenant-scoped
      resources refuse them);
    - any other claim (dotted paths allowed), for example a ``tenant`` user
      attribute mapped to the token.
    """
    issuer = f"{base_url.rstrip('/')}/realms/{realm}"
    return OidcAuthenticator(
        issuer=issuer,
        audience=audience,
        jwks_uri=f"{issuer}/protocol/openid-connect/certs",
        provider="keycloak",
        to_principal=keycloak_principal(realm, client_id=client_id, tenant_claim=tenant_claim),
        http=http,
    )


def keycloak_principal(
    realm: str, *, client_id: str | None = None, tenant_claim: str | None = None
) -> Any:
    """Keycloak claims (from a token or an introspection answer) → ``Principal``."""
    builtin = {*KEYCLOAK_BUILTIN_ROLES, f"default-roles-{realm}"}

    def to_principal(claims: Claims) -> Principal:
        client = client_id or claims.get("azp")
        realm_roles = claim_path(claims, "realm_access.roles") or []
        client_roles = (
            (claim_path(claims, f"resource_access.{client}.roles") or []) if client else []
        )
        username = claims.get("preferred_username") or ""
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            provider="keycloak",
            name=claims.get("name") or username or None,
            email=claims.get("email"),
            roles=frozenset(r for r in [*realm_roles, *client_roles] if r not in builtin),
            groups=split(claims.get("groups")),
            scopes=split(claims.get("scope")),
            client_id=claims.get("azp"),
            service=username.startswith("service-account-"),
            tenant=single_tenant(claim_path(claims, tenant_claim)) if tenant_claim else None,
            claims=claims,
        )

    return to_principal


# ------------------------------------------------------------ Amazon Cognito


def cognito(
    region: str,
    user_pool_id: str,
    *,
    client_ids: str | Collection[str],
    token_use: str = "access",
    http: httpx.AsyncClient | None = None,
) -> OidcAuthenticator:
    """Amazon Cognito user pool.

    Access tokens have no ``aud``: the app client is checked through
    ``client_id`` (and ``aud`` for ID tokens), together with ``token_use``.
    Groups (``cognito:groups``) become roles. Client-credentials (machine) tokens
    are marked ``service``.
    """
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    allowed = {client_ids} if isinstance(client_ids, str) else set(client_ids)

    def check(claims: Claims) -> None:
        if claims.get("token_use") != token_use:
            raise AuthenticationFailed(
                f"Expected a Cognito {token_use} token", reason="invalid_token"
            )
        client = claims.get("client_id") if token_use == "access" else claims.get("aud")
        if client not in allowed:
            raise AuthenticationFailed(
                "The token was issued for another app client", reason="invalid_audience"
            )

    def to_principal(claims: Claims) -> Principal:
        groups = split(claims.get("cognito:groups"))
        username = claims.get("username") or claims.get("cognito:username")
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            provider="cognito",
            name=claims.get("name") or username,
            email=claims.get("email"),
            roles=groups,
            groups=groups,
            scopes=split(claims.get("scope")),
            client_id=claims.get("client_id") or claims.get("aud"),
            service=username is None and token_use == "access",
            claims=claims,
        )

    return OidcAuthenticator(
        issuer=issuer,
        audience=list(allowed) if token_use == "id" else None,
        jwks_uri=f"{issuer}/.well-known/jwks.json",
        provider="cognito",
        to_principal=to_principal,
        check=check,
        http=http,
    )


# --------------------------------------------------------- Microsoft Entra ID

_ENTRA_ISSUER = re.compile(r"^https://login\.microsoftonline\.com/([0-9a-fA-F-]{36})/v2\.0$")
_MULTI_TENANT = {"common", "organizations", "consumers"}


def entra_id(
    tenant: str,
    *,
    audience: str | Sequence[str],
    allowed_tenants: Collection[str] | None = None,
    http: httpx.AsyncClient | None = None,
) -> OidcAuthenticator:
    """Microsoft Entra ID (v2.0 tokens; set ``accessTokenAcceptedVersion: 2`` in the
    app manifest).

    ``tenant`` is a tenant id, or ``organizations``/``common`` for multi-tenant
    apps; then only issuers of ``allowed_tenants`` are accepted (all tenants when
    ``None``, which you rarely want). The subject is the ``oid`` (stable across
    apps of the tenant); app roles go to ``roles``, group ids to ``groups`` and
    delegated scopes (``scp``) to ``scopes``. App-only tokens are ``service``.
    """
    multi = tenant in _MULTI_TENANT
    allowed = {t.lower() for t in allowed_tenants} if allowed_tenants is not None else None
    if not multi and allowed is None:
        allowed = {tenant.lower()}

    def issuer_ok(iss: str, claims: Claims) -> bool:
        match = _ENTRA_ISSUER.match(iss)
        if not match:
            return False
        tid = match.group(1).lower()
        if str(claims.get("tid", "")).lower() != tid:
            return False
        return allowed is None or tid in allowed

    def to_principal(claims: Claims) -> Principal:
        scopes = split(claims.get("scp"))
        return Principal(
            subject=str(claims.get("oid") or claims["sub"]),
            issuer=str(claims["iss"]),
            provider="entra_id",
            name=claims.get("name"),
            email=claims.get("email") or claims.get("preferred_username"),
            roles=split(claims.get("roles")),
            groups=split(claims.get("groups")),
            scopes=scopes,
            tenant=claims.get("tid"),
            client_id=claims.get("azp") or claims.get("appid"),
            service=claims.get("idtyp") == "app" or not scopes,
            claims=claims,
        )

    keys_tenant = "common" if multi else tenant
    return OidcAuthenticator(
        issuer=issuer_ok,
        audience=audience,
        jwks_uri=f"https://login.microsoftonline.com/{keys_tenant}/discovery/v2.0/keys",
        provider="entra_id",
        to_principal=to_principal,
        required_claims=("exp", "iat", "sub", "iss", "tid"),
        http=http,
    )


# ------------------------------------------------------------- Google Cloud

GOOGLE_SECURETOKEN_JWKS = (
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
)
GOOGLE_OAUTH_JWKS = "https://www.googleapis.com/oauth2/v3/certs"


def google_identity_platform(
    project_id: str,
    *,
    roles_claim: str = "roles",
    tenants: Collection[str] | None = None,
    http: httpx.AsyncClient | None = None,
) -> OidcAuthenticator:
    """Google Cloud Identity Platform / Firebase Authentication ID tokens.

    Roles come from a custom claim (``roles_claim``, set with the Admin SDK's
    ``set_custom_user_claims``). With multi-tenancy, ``tenants`` restricts the
    accepted ``firebase.tenant`` values.
    """
    issuer = f"https://securetoken.google.com/{project_id}"

    def check(claims: Claims) -> None:
        tenant = claim_path(claims, "firebase.tenant")
        if tenants is not None and tenant not in tenants:
            raise AuthenticationFailed(
                "The token belongs to another tenant", reason="invalid_tenant"
            )

    def to_principal(claims: Claims) -> Principal:
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            provider="google_identity_platform",
            name=claims.get("name"),
            email=claims.get("email"),
            roles=split(claim_path(claims, roles_claim)),
            tenant=claim_path(claims, "firebase.tenant"),
            claims=claims,
        )

    return OidcAuthenticator(
        issuer=issuer,
        audience=project_id,
        jwks_uri=GOOGLE_SECURETOKEN_JWKS,
        provider="google_identity_platform",
        to_principal=to_principal,
        check=check,
        http=http,
    )


def google_id_token(
    audience: str | Sequence[str],
    *,
    service_accounts: Collection[str] | None = None,
    http: httpx.AsyncClient | None = None,
) -> OidcAuthenticator:
    """ID tokens signed by Google (``accounts.google.com``): Cloud Run / Cloud
    Functions service-to-service calls, Pub/Sub push with OIDC, Google sign-in.

    ``service_accounts`` restricts the accepted ``email`` claim (recommended for
    machine callers).
    """

    def issuer_ok(iss: str, claims: Claims) -> bool:
        return iss in ("https://accounts.google.com", "accounts.google.com")

    def check(claims: Claims) -> None:
        if service_accounts is not None and claims.get("email") not in service_accounts:
            raise AuthenticationFailed(
                "Caller service account not allowed", reason="forbidden_caller"
            )
        if claims.get("email") and claims.get("email_verified") is False:
            raise AuthenticationFailed("The email is not verified", reason="invalid_token")

    def to_principal(claims: Claims) -> Principal:
        email = claims.get("email")
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            provider="google",
            name=claims.get("name"),
            email=email,
            tenant=claims.get("hd"),
            client_id=claims.get("azp"),
            service=bool(email and email.endswith(".gserviceaccount.com")),
            claims=claims,
        )

    return OidcAuthenticator(
        issuer=issuer_ok,
        audience=audience,
        jwks_uri=GOOGLE_OAUTH_JWKS,
        provider="google",
        to_principal=to_principal,
        check=check,
        http=http,
    )
