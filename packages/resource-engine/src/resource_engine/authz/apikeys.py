"""API keys for machine clients that cannot use OAuth (scripts, partners'
servers, cron jobs). See docs/api-keys.md.

```python
registry.register_api_keys()
keys = ApiKeyAuthenticator(uow_factory, fallback=oidc)   # rek_ tokens here, the rest to OIDC
mount_resources(app, registry, manager, actor_dependency=bearer_actor(keys))
```

A key is ``rek_<id>_<secret>``, sent as ``Authorization: Bearer <key>`` (or
``X-API-Key``). Only a SHA-256 hash of the secret is stored, and the full key
is returned once, by ``create``. Each key acts as a principal
(``apikey:<id>``) with the roles and tenant it was created with: at most the
creator's permissions. Keys expire (``expires_at``) and are revoked with the
``revoke`` transition, both audited like any change.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from statemachine import State, StateMachine

from resource_engine.application.context import OperationContext
from resource_engine.application.resource import Resource
from resource_engine.errors import AuthenticationFailed, ConfigurationError, InvalidPolicy
from resource_engine.ports.identity import Principal

PREFIX = "rek_"
PROVIDER = "apikey"
RESOURCE = "authz_api_keys"
ISSUER = "resource-engine"


@dataclass
class ApiKey:
    """A machine client's key. Only the hash of its secret is stored."""

    id: str
    name: str
    secret_hash: str
    roles: list[str] = field(default_factory=list)
    tenant: str | None = None
    status: str | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    created_by: str | None = None


class ApiKeyStates(StateMachine):
    """Lifecycle of an API key.

    States:
        active: The key authenticates requests (until it expires).
        revoked: The key no longer authenticates. Final.

    Transitions:
        revoke: Withdraw the key at once.
    """

    active = State("Active", initial=True)
    revoked = State("Revoked", final=True)
    revoke = active.to(revoked)


class ApiKeyCreate(BaseModel):
    """A new key for a machine client."""

    name: str = Field(min_length=1, max_length=200, description="What the key is for")
    roles: list[str] = Field(
        min_length=1, description="Roles the key acts with (at most the creator's)"
    )
    expires_at: datetime | None = Field(default=None, description="When it stops working")
    tenant: str | None = Field(
        default=None, description="Tenant it acts in (default: the creator's)"
    )


class ApiKeyUpdate(BaseModel):
    """Rename a key or move its expiry (roles never change: create another key)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    """An API key, without its secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Key id: the key starts with rek_<id>_")
    name: str
    roles: list[str]
    tenant: str | None = None
    status: str = Field(description="active or revoked")
    expires_at: datetime | None = None
    last_used_at: datetime | None = Field(default=None, description="Last request (to a minute)")
    created_at: datetime | None = None
    created_by: str | None = None
    key: str | None = Field(
        default=None,
        description="The full key, only in the create response: store it now, it is not "
        "shown again",
    )


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse(token: str) -> tuple[str, str] | None:
    """``rek_<id>_<secret>`` → ``(id, secret)``."""
    if not token.startswith(PREFIX):
        return None
    key_id, sep, secret = token[len(PREFIX) :].partition("_")
    return (key_id, secret) if sep and key_id and secret else None


async def _check_new_key(ctx: OperationContext) -> None:
    """The roles exist and grant nothing the creator lacks; the tenant is the
    creator's unless a cross-tenant caller names another."""
    from resource_engine.authz.resources import _actor_id, _require_held
    from resource_engine.tenancy import is_cross_tenant

    if "authz" not in ctx.services:
        raise ConfigurationError(
            "API keys need an authorizer: pass authorizer= to the ResourceManager",
            doc="docs/api-keys.md",
        )
    authz = ctx.service("authz")
    key: ApiKey = ctx.entity
    actions: set[str] = set()
    store = getattr(authz, "store", None)
    policies = await store.policies() if store is not None else []
    for role in key.roles:
        system = authz.config.system_role(role)
        if system is not None:
            for grant in system.grants:
                actions |= set(
                    authz.catalog.matching(grant, include_sensitive=system.include_sensitive)
                )
            continue
        if await ctx.repository("authz_roles").get(role) is None:
            raise InvalidPolicy(
                f"Unknown role {role!r}",
                details={"problems": [{"path": "$.roles", "message": f"no role {role!r}"}]},
            )
        for policy in policies:
            if policy.effect == "allow" and role in (policy.subjects or {}).get("roles", []):
                for pattern in policy.actions:
                    actions |= set(authz.catalog.matching(pattern))
    await _require_held(ctx, sorted(actions))
    own = getattr(ctx.actor, "tenant", None)
    if key.tenant is None:
        key.tenant = own
    elif key.tenant != own and not await is_cross_tenant(ctx.actor, authz):
        raise InvalidPolicy(
            f"You cannot create keys for tenant {key.tenant!r}",
            details={"problems": [{"path": "$.tenant", "message": "not your tenant"}]},
        )
    key.created_at = datetime.now(UTC)
    key.created_by = _actor_id(ctx)


class ApiKeyResource(Resource):
    """API keys for machine clients that cannot use OAuth.

    Business area:
        Permissions
    """

    name = RESOURCE
    model = ApiKey
    id_type = str
    schemas = {"create": ApiKeyCreate, "update": ApiKeyUpdate, "read": ApiKeyRead}
    operations = ["list", "get", "create", "update"]
    operation_docs = {
        "list": "Lists the API keys (never their secrets).",
        "get": "Returns an API key (never its secret).",
        "create": "Creates an API key; the response carries the full key, once.",
        "update": "Renames a key or changes its expiry.",
    }
    state_machine = ApiKeyStates
    filterable = ["status", "tenant", "created_by"]
    sortable = ["name", "created_at", "last_used_at", "expires_at"]
    authz_wildcards = False
    hooks = {"before_create": [_check_new_key]}

    @classmethod
    def build_entity(cls, data: Any, ctx: OperationContext) -> ApiKey:
        key_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        ctx.extras["api_key"] = f"{PREFIX}{key_id}_{secret}"
        ctx.extras["never_store"] = {"key"}  # not in idempotency records either
        return ApiKey(
            id=key_id,
            name=data.name,
            roles=sorted(set(data.roles)),
            tenant=data.tenant,
            expires_at=data.expires_at,
            secret_hash=hash_secret(secret),
        )

    @classmethod
    def to_read(cls, entity: Any, ctx: OperationContext) -> Any:
        read = ApiKeyRead.model_validate(entity, from_attributes=True)
        if ctx.operation == "create" and "api_key" in ctx.extras:
            read.key = ctx.extras["api_key"]
        return read


class ApiKeyAuthenticator:
    """An ``Authenticator`` for API keys; other tokens go to ``fallback``.

    Args:
        uow_factory: To read the keys (``authz_api_keys``).
        fallback: Authenticator for tokens that are not API keys (OIDC);
            without one they are refused.
        touch_every: Seconds between ``last_used_at`` updates of one key.
    """

    def __init__(
        self,
        uow_factory: Any,
        *,
        fallback: Any = None,
        resource: str = RESOURCE,
        touch_every: float = 60.0,
        clock: Any = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.fallback = fallback
        self.resource = resource
        self.touch_every = touch_every
        self.clock = clock or (lambda: datetime.now(UTC))
        self._touched: dict[str, float] = {}

    async def authenticate(self, token: str) -> Principal:
        parsed = parse(token)
        if parsed is None:
            if self.fallback is not None and not token.startswith(PREFIX):
                return await self.fallback.authenticate(token)
            raise AuthenticationFailed("Invalid API key", reason="invalid")
        key_id, secret = parsed
        async with self.uow_factory() as uow:
            key = await uow.repository(self.resource).get(key_id)
            if key is None or not hmac.compare_digest(key.secret_hash, hash_secret(secret)):
                raise AuthenticationFailed("Invalid API key", reason="invalid")
            if key.status != "active":
                raise AuthenticationFailed("The API key was revoked", reason="revoked")
            now = self.clock()
            if key.expires_at is not None and _aware(key.expires_at) <= now:
                raise AuthenticationFailed("The API key expired", reason="expired")
            principal = Principal(  # before the commit: the record may expire then
                subject=key.id,
                issuer=ISSUER,
                provider=PROVIDER,
                name=key.name,
                roles=frozenset(key.roles),
                tenant=key.tenant,
                client_id=key.id,
                service=True,
            )
            if time.monotonic() - self._touched.get(key.id, -1e9) >= self.touch_every:
                self._touched[key.id] = time.monotonic()
                key.last_used_at = now
                await uow.repository(self.resource).update(key)
                await uow.commit()
        return principal


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "ApiKey",
    "ApiKeyAuthenticator",
    "ApiKeyCreate",
    "ApiKeyRead",
    "ApiKeyResource",
    "ApiKeyUpdate",
    "hash_secret",
]
