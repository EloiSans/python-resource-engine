"""API keys: created with at most the creator's permissions, shown once, stored
hashed, revoked and expired; the authenticator routes rek_ tokens to them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import (
    ApiKeyAuthenticator,
    AuthzConfig,
    IdpRoleSource,
    PolicyAuthorizer,
    SystemRole,
)
from resource_engine.errors import (
    AuthenticationFailed,
    InvalidPolicy,
    PermissionDenied,
    PrivilegeEscalation,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class NoteResource(Resource):
    name = "notes"
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "create"]


ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"admins"}))
ADA = Principal(subject="ada", issuer="https://sso", roles=frozenset({"readers"}), tenant="acme")


class Oidc:
    async def authenticate(self, token: str) -> Principal:
        if token == "oidc-token":
            return ADA
        raise AuthenticationFailed("bad token", reason="invalid")


@pytest.fixture
async def setup():
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_authz()
    registry.register_api_keys()
    registry.register_audit()
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        system_roles=[SystemRole.admin()],
        role_source=IdpRoleSource({"admins": "admin", "readers": "reader"}),
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(config, store.unit_of_work),
        audit=True,
    )
    await manager.create("authz_roles", {"name": "reader"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Readers read notes and make keys",
            "effect": "allow",
            "subjects": {"roles": ["reader"]},
            "actions": ["notes:list", "authz_api_keys:create", "authz_api_keys:list"],
        },
        actor=ADMIN,
    )
    keys = ApiKeyAuthenticator(store.unit_of_work, fallback=Oidc())
    return manager, keys


async def test_a_key_acts_with_its_roles(setup):
    manager, keys = setup
    made = await manager.create(
        "authz_api_keys", {"name": "nightly", "roles": ["reader"]}, actor=ADA
    )
    assert made.key.startswith(f"rek_{made.id}_") and made.status == "active"
    assert (made.tenant, made.created_by) == ("acme", "oidc:ada")
    stored = await manager.get("authz_api_keys", made.id, actor=ADMIN)
    assert stored.key is None and "secret_hash" not in stored.model_dump()  # shown once

    robot = await keys.authenticate(made.key)
    assert (robot.id, robot.service, robot.tenant, robot.roles) == (
        f"apikey:{made.id}",
        True,
        "acme",
        frozenset({"reader"}),
    )
    await manager.list("notes", actor=robot)
    with pytest.raises(PermissionDenied):
        await manager.create("notes", {"text": "x"}, actor=robot)
    assert (await manager.get("authz_api_keys", made.id, actor=ADMIN)).last_used_at is not None
    assert (await keys.authenticate("oidc-token")) == ADA  # other tokens: the fallback


async def test_no_escalation_and_known_roles(setup):
    manager, _ = setup
    with pytest.raises(PrivilegeEscalation):
        await manager.create("authz_api_keys", {"name": "x", "roles": ["admin"]}, actor=ADA)
    with pytest.raises(InvalidPolicy, match="Unknown role"):
        await manager.create("authz_api_keys", {"name": "x", "roles": ["ghost"]}, actor=ADMIN)
    with pytest.raises(InvalidPolicy, match="tenant"):
        await manager.create(
            "authz_api_keys", {"name": "x", "roles": ["reader"], "tenant": "globex"}, actor=ADA
        )
    admin_key = await manager.create(
        "authz_api_keys", {"name": "ops", "roles": ["admin"], "tenant": "globex"}, actor=ADMIN
    )
    assert admin_key.tenant == "globex"  # cross-tenant callers choose


async def test_revoked_expired_and_forged_keys_fail(setup):
    manager, keys = setup
    made = await manager.create("authz_api_keys", {"name": "a", "roles": ["reader"]}, actor=ADMIN)
    key_id, secret = made.key[len("rek_") :].split("_", 1)
    for forged in (f"rek_{key_id}_{secret}x", f"rek_nope_{secret}", "rek_broken"):
        with pytest.raises(AuthenticationFailed, match="Invalid API key"):
            await keys.authenticate(forged)
    await manager.transition("authz_api_keys", made.id, "revoke", actor=ADMIN)
    with pytest.raises(AuthenticationFailed, match="revoked"):
        await keys.authenticate(made.key)

    soon = datetime.now(UTC) + timedelta(hours=1)
    short = await manager.create(
        "authz_api_keys", {"name": "b", "roles": ["reader"], "expires_at": soon}, actor=ADMIN
    )
    await keys.authenticate(short.key)
    later = ApiKeyAuthenticator(manager.uow_factory, clock=lambda: soon + timedelta(seconds=1))
    with pytest.raises(AuthenticationFailed, match="expired"):
        await later.authenticate(short.key)
    with pytest.raises(AuthenticationFailed):  # no fallback: other tokens are refused
        await later.authenticate("oidc-token")
    history = await manager.history("authz_api_keys", made.id, actor=ADMIN)
    assert [c.event for c in history] == [None, "revoke"]


async def test_the_key_is_not_kept_for_idempotent_replays(setup):
    manager, keys = setup
    data = {"name": "ci", "roles": ["reader"]}
    first = await manager.create("authz_api_keys", data, actor=ADMIN, idempotency_key="k-1")
    replay = await manager.create("authz_api_keys", data, actor=ADMIN, idempotency_key="k-1")
    assert first.key and replay.id == first.id and replay.key is None
    await keys.authenticate(first.key)
