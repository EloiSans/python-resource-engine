from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, system_principal
from resource_engine.errors import (
    ConfigurationError,
    EntityNotFound,
    TenantMismatch,
    TenantRequired,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


@dataclass
class Order:
    item: str
    tenant_id: str | None = None
    id: int | None = None


class OrderIn(BaseModel):
    item: str


class OrderPatch(BaseModel):
    item: str | None = None


class OrderOut(OrderIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant_id: str


class OrderResource(Resource):
    model = Order
    schemas = {"create": OrderIn, "update": OrderPatch, "read": OrderOut}
    operations = ["list", "get", "create", "update", "delete"]
    filterable = ["item", "tenant_id"]
    tenant_field = "tenant_id"
    documents = [DocumentSlot("receipt")]


@dataclass
class Country:
    code: str
    id: int | None = None


class CountryIn(BaseModel):
    code: str


class CountryOut(CountryIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CountryResource(Resource):
    name = "countries"
    model = Country
    schemas = {"create": CountryIn, "read": CountryOut}
    operations = ["list", "get", "create"]


def who(name: str, tenant: str | None, *roles: str) -> Principal:
    return Principal(subject=name, issuer="idp", tenant=tenant, roles=frozenset(roles))


ADMIN = who("root", None, "admins")
ANNA = who("anna", "t1", "staff")
BORIS = who("boris", "t2", "staff")
NOBODY = who("nobody", None, "staff")


@pytest.fixture
async def manager():
    registry = ResourceRegistry()
    registry.register(OrderResource)
    registry.register(CountryResource)
    registry.register_documents()
    registry.register_authz()
    registry.register_audit()
    store = InMemoryStore.from_registry(registry)
    authz = PolicyAuthorizer(
        AuthzConfig(role_source=IdpRoleSource({"admins": "admin", "staff": "staff"})),
        store.unit_of_work,
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=authz,
        blob_store=InMemoryBlobStore(),
        audit=True,
    )
    await manager.create("authz_roles", {"name": "staff"}, actor=ADMIN)
    await manager.create(  # deliberately broad: tenancy must hold without policy help
        "authz_policies",
        {
            "name": "Staff do everything",
            "effect": "allow",
            "subjects": {"roles": ["staff"]},
            "actions": ["orders:*", "countries:*"],
        },
        actor=ADMIN,
    )
    return manager


async def test_confined_callers_only_see_their_tenant(manager):
    a = await manager.create("orders", {"item": "pen"}, actor=ANNA)
    b = await manager.create("orders", {"item": "ink"}, actor=BORIS)
    assert a.tenant_id == "t1" and b.tenant_id == "t2"  # stamped from the caller
    page = await manager.list("orders", actor=ANNA)
    assert [o.item for o in page.items] == ["pen"] and page.total == 1
    conflicting = ListQuery(filters={"tenant_id": "t2"})
    assert (await manager.list("orders", conflicting, actor=ANNA)).total == 0
    for operation in (
        manager.get("orders", b.id, actor=ANNA),
        manager.update("orders", b.id, {"item": "x"}, actor=ANNA),
        manager.delete("orders", b.id, actor=ANNA),
    ):
        with pytest.raises(EntityNotFound):
            await operation
    assert (await manager.get("orders", b.id, actor=BORIS)).item == "ink"
    assert await manager.can(ANNA, "orders", "get", b.id) is False
    assert await manager.can(BORIS, "orders", "get", b.id) is True


async def test_callers_without_tenant_and_other_tenants_are_refused(manager):
    with pytest.raises(TenantRequired):
        await manager.list("orders", actor=NOBODY)
    with pytest.raises(TenantRequired):
        await manager.list("orders")  # anonymous
    with pytest.raises(TenantMismatch):
        await manager.list("orders", actor=ANNA, tenant="t2")
    assert (await manager.list("countries", actor=NOBODY)).total == 0  # not tenant-scoped


async def test_cross_tenant_callers(manager):
    await manager.create("orders", {"item": "pen"}, actor=ANNA)
    await manager.create("orders", {"item": "ink"}, actor=BORIS)
    assert (await manager.list("orders", actor=ADMIN)).total == 2
    narrowed = await manager.list("orders", actor=ADMIN, tenant="t2")
    assert [o.item for o in narrowed.items] == ["ink"]
    with pytest.raises(TenantRequired):  # creating needs an explicit tenant
        await manager.create("orders", {"item": "cap"}, actor=ADMIN)
    created = await manager.create("orders", {"item": "cap"}, actor=ADMIN, tenant="t3")
    assert created.tenant_id == "t3"
    job = system_principal("import")
    assert (await manager.list("orders", actor=job)).total == 3
    assert (await manager.get("orders", created.id, actor=job, tenant="t3")).item == "cap"
    with pytest.raises(EntityNotFound):
        await manager.get("orders", created.id, actor=job, tenant="t1")


async def test_documents_follow_the_owner_tenant(manager):
    order = await manager.create("orders", {"item": "pen"}, actor=ANNA)
    doc = await manager.documents.attach(
        "orders",
        order.id,
        "receipt",
        b"x",
        filename="r.txt",
        content_type="text/plain",
        actor=ANNA,
    )
    with pytest.raises(EntityNotFound):
        await manager.documents.attach(
            "orders",
            order.id,
            "receipt",
            b"y",
            filename="r.txt",
            content_type="text/plain",
            actor=BORIS,
        )
    with pytest.raises(EntityNotFound):
        await manager.documents.get(doc.id, actor=BORIS)
    with pytest.raises(EntityNotFound):
        await manager.documents.list("orders", order.id, actor=BORIS)
    assert (await manager.documents.get(doc.id, actor=ANNA)).id == doc.id


async def test_refusals_are_audited_with_the_tenant(manager):
    order = await manager.create("orders", {"item": "pen"}, actor=ANNA)
    with pytest.raises(EntityNotFound):
        await manager.get("orders", order.id, actor=BORIS)
    with pytest.raises(TenantRequired):
        await manager.list("orders", actor=NOBODY)
    log = await manager.list(
        "audit_log", ListQuery(filters={"resource": "orders"}, order_by=["at"]), actor=ADMIN
    )
    assert [(e.outcome, e.tenant, e.details.get("reason")) for e in log.items] == [
        ("ok", "t1", None),
        ("denied", "t2", "other tenant"),
        ("denied", None, "no tenant"),
    ]


async def test_without_an_authorizer_only_jobs_cross_tenants():
    registry = ResourceRegistry()
    registry.register(OrderResource)
    registry.register_documents()
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, blob_store=InMemoryBlobStore())
    await manager.create("orders", {"item": "pen"}, actor=ANNA)
    await manager.create("orders", {"item": "cap"}, actor=system_principal(), tenant="t2")
    assert (await manager.list("orders", actor=ANNA)).total == 1
    assert (await manager.list("orders", actor=system_principal())).total == 2
    with pytest.raises(TenantRequired):
        await manager.list("orders", actor=ADMIN)  # no roles to resolve: confined, no tenant


def test_tenant_field_cannot_be_updated():
    class Movable(OrderResource):
        name = "movable"

        class Patch(BaseModel):
            tenant_id: str | None = None

        schemas = {"create": OrderIn, "update": Patch, "read": OrderOut}

    with pytest.raises(ConfigurationError):
        Movable.definition()
