"""Permissions on the example: system roles from code, policies as data."""

from __future__ import annotations

import pytest
from invoices.authz import seed
from invoices.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer, system_principal
from resource_engine.errors import EntityNotFound, FieldNotWritable, PermissionDenied
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore

ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"platform-admin"}))
ACCOUNTANT = Principal(subject="ada", issuer="https://sso", roles=frozenset({"accountant"}))
APPROVER = Principal(subject="pau", issuer="https://sso", roles=frozenset({"approver"}))
ISSUER = Principal(subject="eli", issuer="https://sso", roles=frozenset({"issuer"}))


@pytest.fixture
async def manager():
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        audit=True,
    )
    await seed(manager, ADMIN)
    return manager


class _Silent:
    async def send(self, message):
        return None


async def submitted(manager, amount):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": amount}, actor=ADMIN)
    await manager.transition("invoices", inv.id, "submit", actor=ADMIN)
    await manager.documents.attach(
        "invoices",
        inv.id,
        "signed_pdf",
        b"%PDF",
        filename="s.pdf",
        content_type="application/pdf",
        actor=ADMIN,
    )
    return inv.id


async def test_accountant_is_read_only(manager):
    invoice_id = await submitted(manager, 500)
    assert (await manager.list("invoices", actor=ACCOUNTANT)).total == 1
    with pytest.raises(PermissionDenied):
        await manager.transition("invoices", invoice_id, "approve", actor=ACCOUNTANT)


async def test_approver_sees_pending_and_approves_small_amounts(manager):
    small = await submitted(manager, 500)
    big = await submitted(manager, 25_000)
    draft = (await manager.create("invoices", {"customer": "X", "amount": 1}, actor=ADMIN)).id
    assert {i.id for i in (await manager.list("invoices", actor=APPROVER)).items} == {small, big}
    with pytest.raises(EntityNotFound):
        await manager.get("invoices", draft, actor=APPROVER)
    with pytest.raises(PermissionDenied):
        await manager.transition("invoices", big, "approve", actor=APPROVER)
    approved = await manager.transition("invoices", small, "approve", actor=APPROVER)
    assert approved.status == "approved"


async def test_issuer_runs_the_invoice_but_cannot_approve_it(manager):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": 10}, actor=ISSUER)
    await manager.update("invoices", invoice.id, {"amount": 12}, actor=ISSUER)
    await manager.transition("invoices", invoice.id, "submit", actor=ISSUER)
    with pytest.raises(PermissionDenied):
        await manager.transition("invoices", invoice.id, "approve", actor=ISSUER)


async def test_engine_jobs_run_as_the_system_principal(manager):
    inv = await manager.create(
        "invoices", {"customer": "ACME", "amount": 1}, actor=system_principal("billing-job")
    )
    assert inv.id


async def test_internal_notes_are_restricted(manager):
    invoice = await manager.create(
        "invoices", {"customer": "ACME", "amount": 10, "notes": "call first"}, actor=ADMIN
    )
    await manager.transition("invoices", invoice.id, "submit", actor=ADMIN)
    assert "notes" not in (await manager.get("invoices", invoice.id, actor=ACCOUNTANT)).model_dump()
    assert (await manager.get("invoices", invoice.id, actor=APPROVER)).notes == "call first"
    assert (await manager.get("invoices", invoice.id, actor=ADMIN)).notes == "call first"
    fields = await manager.authorizer.field_permissions(ACCOUNTANT)
    assert "notes" in fields["invoices"]["hidden"]


async def test_conditional_field_rule(manager, monkeypatch):
    clerk = Principal(subject="cl", issuer="https://sso", roles=frozenset({"clerk"}))
    monkeypatch.setitem(manager.authorizer.role_source.mapping, "clerk", "clerk")
    await manager.create("authz_roles", {"name": "clerk"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Clerks edit invoices",
            "effect": "allow",
            "subjects": {"roles": ["clerk"]},
            "actions": ["invoices:get", "invoices:update", "invoices:transition:submit"],
        },
        actor=ADMIN,
    )
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": 10}, actor=ADMIN)
    await manager.update("invoices", invoice.id, {"amount": 20}, actor=clerk)  # draft: allowed
    await manager.transition("invoices", invoice.id, "submit", actor=clerk)
    with pytest.raises(FieldNotWritable):
        await manager.update("invoices", invoice.id, {"amount": 30}, actor=clerk)


async def test_audit_trail(manager):
    from resource_engine import ListQuery

    invoice_id = await submitted(manager, 50_000)
    with pytest.raises(PermissionDenied):  # above the approvers' limit
        await manager.transition("invoices", invoice_id, "approve", actor=APPROVER)
    await manager.transition("invoices", invoice_id, "approve", actor=ADMIN)
    history = await manager.list(
        "audit_log",
        ListQuery(filters={"resource": "invoices", "entity_id": str(invoice_id)}, order_by=["at"]),
        actor=ADMIN,
    )
    rows = [(e.operation, e.action, e.outcome, e.actor) for e in history.items]
    assert rows[-2:] == [
        ("transition", "transition:approve", "denied", APPROVER.id),
        ("transition", "transition:approve", "ok", ADMIN.id),
    ]
    assert ("attach", "attach:signed_pdf", "ok", ADMIN.id) in rows
    assert history.items[-1].changes == {"status": {"from": "pending", "to": "approved"}}
    with pytest.raises(PermissionDenied):
        await manager.list("audit_log", actor=ACCOUNTANT)
