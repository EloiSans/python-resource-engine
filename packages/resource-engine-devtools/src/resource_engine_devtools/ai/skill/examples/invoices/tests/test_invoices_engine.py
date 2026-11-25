"""Lifecycle tests of the reference module against in-memory adapters."""

from __future__ import annotations

from decimal import Decimal

import pytest
from invoices.application.customers import CustomerResource
from invoices.application.resource import InvoiceResource
from resource_engine import ListQuery, OperationContext, ResourceManager, ResourceRegistry
from resource_engine.domain import DomainError
from resource_engine.errors import (
    EntityNotFound,
    InvalidQuery,
    OperationNotEnabled,
    RequiredDocumentMissing,
    TransitionNotAllowed,
    UnknownTransition,
    ValidationFailed,
)
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


async def test_create_sets_initial_state_and_publishes_after_commit(manager, publisher):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "100.00"})
    assert invoice.id == 1
    assert invoice.status == "draft"
    assert publisher.names() == ["ResourceCreated"]


async def test_validation_error_has_details(manager):
    with pytest.raises(ValidationFailed) as info:
        await manager.create("invoices", {"customer": "", "amount": "x"})
    fields = {tuple(e["loc"]) for e in info.value.details}
    assert ("customer",) in fields and ("amount",) in fields


async def test_domain_rule_in_hook_rolls_back(manager, store, publisher):
    with pytest.raises(DomainError):
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    assert store.all("invoices") == []
    assert publisher.events == []


async def test_update_only_sent_fields_and_reports_changes(manager, publisher):
    created = await manager.create("invoices", {"customer": "ACME", "amount": "10", "notes": "a"})
    updated = await manager.update("invoices", created.id, {"amount": "12"})
    assert updated.amount == Decimal("12") and updated.notes == "a"
    event = publisher.events[-1]
    assert event.name == "ResourceUpdated"
    assert event.changes == {"amount": (Decimal("10"), Decimal("12"))}


async def test_after_hook_failure_rolls_back_update(manager, store):
    created = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    with pytest.raises(DomainError):
        await manager.update("invoices", created.id, {"amount": "-5"})
    assert store.all("invoices")[0].amount == Decimal("10")


async def test_state_cannot_change_through_update(manager):
    created = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    updated = await manager.update("invoices", created.id, {"status": "paid", "notes": "x"})
    assert updated.status == "draft"


async def test_full_transition_flow(manager, publisher, notifier):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    assert await manager.allowed_transitions("invoices", inv.id) == ["submit"]
    inv = await manager.transition("invoices", inv.id, "submit")
    assert inv.status == "pending"
    with pytest.raises(RequiredDocumentMissing):
        await manager.transition("invoices", inv.id, "approve")
    await attach_signed(manager, inv.id)
    inv = await manager.transition("invoices", inv.id, "approve")
    assert inv.status == "approved"
    assert notifier.sent == [f"Invoice {inv.id} approved"]
    inv = await manager.transition("invoices", inv.id, "pay")
    assert inv.status == "paid"
    transitions = [(e.source, e.target) for e in publisher.events if e.name == "TransitionExecuted"]
    assert transitions == [("draft", "pending"), ("pending", "approved"), ("approved", "paid")]


async def test_invalid_transition_is_rejected_with_allowed_list(manager, store):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    with pytest.raises(TransitionNotAllowed) as info:
        await manager.transition("invoices", inv.id, "pay")
    assert info.value.details == {"state": "draft", "allowed": ["submit"]}
    assert store.all("invoices")[0].status == "draft"


async def test_unknown_transition(manager):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    with pytest.raises(UnknownTransition):
        await manager.transition("invoices", inv.id, "archive")


async def test_transition_payload_reaches_state_machine(manager):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", inv.id, "submit")
    with pytest.raises(ValidationFailed):
        await manager.transition("invoices", inv.id, "reject", {})
    inv = await manager.transition("invoices", inv.id, "reject", {"reason": "Wrong VAT"})
    assert inv.status == "rejected" and inv.rejection_reason == "Wrong VAT"
    inv = await manager.transition("invoices", inv.id, "reopen")
    assert inv.status == "draft" and inv.rejection_reason is None


async def test_submitted_invoice_cannot_be_edited_or_deleted(manager):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", inv.id, "submit")
    with pytest.raises(DomainError):
        await manager.update("invoices", inv.id, {"notes": "late"})
    with pytest.raises(DomainError):
        await manager.delete("invoices", inv.id)


async def test_delete_and_not_found(manager, publisher):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.delete("invoices", inv.id)
    assert publisher.names()[-1] == "ResourceDeleted"
    with pytest.raises(EntityNotFound) as info:
        await manager.get("invoices", inv.id)
    assert info.value.code == "RE-E102"


async def test_list_filters_ordering_and_paging(manager):
    for customer, amount in [("A", "30"), ("B", "10"), ("A", "20")]:
        await manager.create("invoices", {"customer": customer, "amount": amount})
    page = await manager.list(
        "invoices", ListQuery(filters={"customer": "A"}, order_by=["amount"], limit=1)
    )
    assert page.total == 2
    assert [i.amount for i in page.items] == [Decimal("20")]
    with pytest.raises(InvalidQuery):
        await manager.list("invoices", ListQuery(filters={"notes": "x"}))


async def test_action_runs_in_transaction_and_emits_events(manager, publisher):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "200"})
    result = await manager.run_action("invoices", "recalculate_totals", inv.id, {"percent": "15"})
    assert result.amount == Decimal("170.00")
    assert publisher.names()[-2:] == ["InvoiceTotalsRecalculated", "ActionExecuted"]


async def test_after_commit_failure_does_not_undo_operation(store, publisher, caplog):
    class BrokenNotifier:
        async def send(self, message):
            raise RuntimeError("SMTP down")

    from invoices.bootstrap import registry

    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_publisher=publisher,
        services={"notifier": BrokenNotifier()},
        blob_store=InMemoryBlobStore(),
    )
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", inv.id, "submit")
    await attach_signed(manager, inv.id)
    approved = await manager.transition("invoices", inv.id, "approve")
    assert approved.status == "approved"
    assert store.all("invoices")[0].status == "approved"
    assert "after_commit hook" in caplog.text
    assert publisher.names()[-1] == "TransitionExecuted"


async def test_hook_order_is_contractual():
    calls: list[str] = []

    def rec(name):
        def hook(ctx: OperationContext):
            calls.append(name)

        hook.__qualname__ = name
        return hook

    class TracedInvoices(InvoiceResource):
        name = "traced"
        documents = []
        hooks = {
            point: [rec(point)]
            for point in (
                "before_validate",
                "after_validate",
                "before_create",
                "after_create",
                "after_commit",
            )
        }

    registry = ResourceRegistry()
    registry.register(CustomerResource)  # invoices reference customers
    registry.register(TracedInvoices)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    await manager.create("traced", {"customer": "ACME", "amount": "1"})
    assert calls == [
        "before_validate",
        "after_validate",
        "before_create",
        "after_create",
        "after_commit",
    ]


async def test_disabled_operation():
    class ReadOnlyInvoices(InvoiceResource):
        name = "readonly_invoices"
        documents = []
        operations = ["list", "get"]

    registry = ResourceRegistry()
    registry.register(CustomerResource)  # invoices reference customers
    registry.register(ReadOnlyInvoices)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    with pytest.raises(OperationNotEnabled):
        await manager.create("readonly_invoices", {"customer": "ACME", "amount": "1"})


async def attach_signed(manager, invoice_id):
    return await manager.documents.attach(
        "invoices",
        invoice_id,
        "signed_pdf",
        b"%PDF-1.7",
        filename="signada.pdf",
        content_type="application/pdf",
    )


async def test_render_summary_replaces_the_previous_summary(manager):
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.run_action("invoices", "render_summary", inv.id)
    await manager.update("invoices", inv.id, {"customer": "ACME SL"})
    await manager.run_action("invoices", "render_summary", inv.id)
    (summary,) = await manager.documents.list("invoices", inv.id, "summary")
    _, stream = await manager.documents.open(summary.id)
    text = b"".join([c async for c in stream]).decode()
    assert "ACME SL" in text and summary.filename == f"factura-{inv.id}.md"
