"""Identity port: who is calling (authentication). See ``ports.authz`` for what they may do.

Entry adapters turn credentials (a bearer token) into a ``Principal`` with an
``Authenticator`` and pass it to the engine as ``actor``; hooks and actions read
it from ``ctx.actor``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Principal:
    """An authenticated caller, normalised across identity providers.

    Attributes:
        subject: Stable id of the user or service at the provider (``sub``/``oid``).
        issuer: Who vouched for it (token ``iss``).
        provider: Adapter preset name (``keycloak``, ``cognito``, ``entra_id``...).
        name / email: Display data when the token carries it.
        roles: Roles or groups the provider assigned (realm/client roles,
            Cognito groups, Entra app roles, custom claims).
        groups: Group memberships, when the provider separates them from roles.
        scopes: OAuth scopes granted to the token.
        tenant: Tenant / organisation id for multi-tenant providers.
        client_id: The OAuth client the token was issued to.
        service: True for machine identities (client credentials).
        claims: All verified claims, for anything not normalised above.
    """

    subject: str
    issuer: str
    provider: str = "oidc"
    name: str | None = None
    email: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    tenant: str | None = None
    client_id: str | None = None
    service: bool = False
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def id(self) -> str:
        """Label used by the engine (e.g. ``Document.created_by``)."""
        return f"{self.provider}:{self.subject}"

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class Authenticator(Protocol):
    """Verifies credentials and returns the caller.

    Raises ``AuthenticationFailed`` (RE-E701) when the credentials are not valid.
    """

    async def authenticate(self, token: str) -> Principal: ...


@dataclass(frozen=True)
class Credentials:
    """A token with what the request proves about its sender, for
    sender-constrained tokens (DPoP, RFC 9449; mTLS, RFC 8705).

    Attributes:
        token: The access token.
        scheme: ``bearer`` or ``dpop`` (the ``Authorization`` scheme, lower case).
        method / url: The request's method and URL without query or fragment,
            as the client sent them (what a DPoP proof's ``htm``/``htu`` name).
        proof: The ``DPoP`` header (a signed proof JWT), if any.
        client_certificate: The TLS client certificate (DER), if any.
    """

    token: str
    scheme: str = "bearer"
    method: str | None = None
    url: str | None = None
    proof: str | None = None
    client_certificate: bytes | None = field(default=None, repr=False)


class RequestAuthenticator(Authenticator, Protocol):
    """An ``Authenticator`` that also checks the request's proof of possession.

    Entry adapters call ``authenticate_request`` when it exists, and refuse
    the ``DPoP`` scheme otherwise (see ``resource_engine.identity``).
    """

    async def authenticate_request(self, credentials: Credentials) -> Principal: ...
