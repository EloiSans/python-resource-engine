from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.audit import CompositeAuditSink, LoggingAuditSink, RepositoryAuditSink
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, system_principal
from resource_engine.errors import ConfigurationError, FieldNotWritable, PermissionDenied
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from statemachine import State, StateMachine


@dataclass
class Bill:
    customer: str
    amount: Decimal
    iban: str | None = None
    notes: str | None = None
    status: str | None = None
    id: int | None = None


class BillMachine(StateMachine):
    draft = State(initial=True)
    paid = State(final=True)
    pay = draft.to(paid)


class BillIn(BaseModel):
    customer: str
    amount: Decimal
    iban: str | None = None
    notes: str | None = None


class BillPatch(BaseModel):
    customer: str | None = None
    amount: Decimal | None = None
    iban: str | None = None
    notes: str | None = None


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "update": BillPatch, "read": BillOut}
    operations = ["list", "get", "create", "update", "delete"]
    state_machine = BillMachine
    restricted_fields = ["notes"]
    audit_exclude = ["iban"]
    documents = [DocumentSlot("pdf")]


@dataclass
class Tag:
    label: str
    id: int | None = None


class TagIn(BaseModel):
    label: str


class TagOut(TagIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TagResource(Resource):
    model = Tag
    schemas = {"create": TagIn, "read": TagOut}
    operations = ["list", "get", "create"]
    audit = False


class SecretResource(TagResource):
    name = "secrets"
    audit = True
    audit_reads = True


ADMIN = Principal(subject="root", issuer="idp", roles=frozenset({"admins"}))
CLERK = Principal(subject="cl", issuer="idp", roles=frozenset({"clerks"}))


def build(*, authz: bool = True, sink=None):
    registry = ResourceRegistry()
    for cls in (BillResource, TagResource, SecretResource):
        registry.register(cls)
    registry.register_documents()
    registry.register_authz()
    registry.register_audit()
    store = InMemoryStore.from_registry(registry)
    authorizer = (
        PolicyAuthorizer(
            AuthzConfig(role_source=IdpRoleSource({"admins": "admin", "clerks": "clerk"})),
            store.unit_of_work,
        )
        if authz
        else None
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=authorizer,
        blob_store=InMemoryBlobStore(),
        audit=sink(store) if sink else True,
    )
    return manager


async def entries(manager, **filters):
    page = await manager.list(
        "audit_log", ListQuery(filters=filters, order_by=["at"], limit=500), actor=ADMIN
    )
    return page.items


async def test_changes_are_recorded_with_masking():
    manager = build()
    bill = await manager.create(
        "bills",
        {"customer": "ACME", "amount": "10.50", "iban": "ES00", "notes": "vip"},
        actor=ADMIN,
    )
    await manager.update("bills", bill.id, {"amount": "12", "notes": "x"}, actor=ADMIN)
    await manager.transition("bills", bill.id, "pay", actor=system_principal("billing"))
    await manager.documents.attach(
        "bills",
        bill.id,
        "pdf",
        b"%PDF",
        filename="b.pdf",
        content_type="application/pdf",
        actor=ADMIN,
    )
    await manager.delete("bills", bill.id, actor=ADMIN)

    log = await entries(manager, resource="bills")
    assert [e.operation for e in log] == ["create", "update", "transition", "attach", "delete"]
    created, updated, paid, attached, deleted = log
    assert created.actor == ADMIN.id and created.actor_roles == ["admin"]
    assert created.entity_id == str(bill.id) and created.outcome == "ok"
    assert created.changes["amount"] == {"to": "10.50"}
    assert created.changes["iban"] == {"changed": True}  # audit_exclude
    assert created.changes["notes"] == {"changed": True}  # restricted
    assert updated.changes == {"amount": {"from": "10.50", "to": "12"}, "notes": {"changed": True}}
    assert paid.actor == "system:billing" and paid.action == "transition:pay"
    assert paid.changes == {"status": {"from": "draft", "to": "paid"}}
    assert attached.action == "attach:pdf"
    assert attached.details["documents"][0]["event"] == "DocumentAttached"
    assert "customer" in deleted.changes and deleted.changes["iban"] == {"changed": True}
    assert len({e.correlation_id for e in log}) == 5


async def test_denials_are_recorded_although_the_transaction_rolls_back():
    manager = build()
    with pytest.raises(PermissionDenied):
        await manager.create("bills", {"customer": "A", "amount": 1}, actor=CLERK)
    await manager.create("authz_roles", {"name": "clerk"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Clerks edit bills",
            "effect": "allow",
            "subjects": {"roles": ["clerk"]},
            "actions": ["bills:create", "bills:get", "bills:update"],
        },
        actor=ADMIN,
    )
    bill = await manager.create("bills", {"customer": "A", "amount": 1}, actor=CLERK)
    with pytest.raises(FieldNotWritable):
        await manager.update("bills", bill.id, {"notes": "secret"}, actor=CLERK)
    denied = await entries(manager, outcome="denied")
    assert [(e.action, e.details["code"]) for e in denied] == [
        ("create", "RE-E702"),
        ("update", "RE-E706"),
    ]
    assert denied[1].details["fields"] == ["notes"] and denied[1].actor == CLERK.id
    assert denied[1].changes == {}  # nothing was written
    assert (await manager.get("bills", bill.id, actor=ADMIN)).notes is None


async def test_opt_out_and_audited_reads():
    manager = build(authz=False)
    await manager.create("tags", {"label": "a"})
    secret = await manager.create("secrets", {"label": "s"})
    await manager.get("secrets", secret.id)
    await manager.list("secrets")
    assert await entries(manager, resource="tags") == []
    ops = [e.operation for e in await entries(manager, resource="secrets")]
    assert ops == ["create", "get", "list"]
    listed = (await entries(manager, operation="list"))[0]
    assert listed.details["returned"] == 1 and listed.actor is None


async def test_audit_log_is_read_only_and_sensitive():
    manager = build()
    definition = manager.registry.get("audit_log")
    assert definition.operations == ("list", "get")
    viewer = Principal(subject="v", issuer="idp", roles=frozenset({"viewers"}))
    manager.authorizer.role_source.mapping["viewers"] = "viewer"
    with pytest.raises(PermissionDenied):
        await manager.list("audit_log", actor=viewer)


async def test_failing_sinks_never_break_denials_or_reads(caplog):
    class Broken:
        async def record(self, entries, *, uow=None):
            if uow is None:
                raise RuntimeError("sink down")

    manager = build(authz=False, sink=lambda store: Broken())
    secret = await manager.create("secrets", {"label": "s"})
    assert (await manager.get("secrets", secret.id)).label == "s"
    assert "Writing the audit entry failed" in caplog.text


async def test_composite_and_logging_sinks(caplog):
    caplog.set_level("INFO", logger="resource_engine.audit")
    manager = build(
        authz=False,
        sink=lambda store: CompositeAuditSink(
            RepositoryAuditSink(store.unit_of_work), LoggingAuditSink()
        ),
    )
    await manager.create("bills", {"customer": "A", "amount": 1})
    assert len(await entries(manager, resource="bills")) == 1
    assert '"operation": "create"' in caplog.text


def test_sink_needs_the_audit_resource():
    registry = ResourceRegistry()
    registry.register(TagResource)
    store = InMemoryStore.from_registry(registry)
    with pytest.raises(ConfigurationError):
        ResourceManager(registry, store.unit_of_work, audit=True)
