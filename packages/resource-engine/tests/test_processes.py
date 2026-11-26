"""Process managers on the invoices example: the invoice payment process."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from invoices.bootstrap import registry
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, ResourceManager, ResourceRegistry, TimerJob
from resource_engine.errors import ConfigurationError
from resource_engine.messaging import EventEnvelope, MessageProcessor, OutboxDelivery
from resource_engine.messaging.processing import Outcome
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.processes import On, Process, ProcessFeed, ProcessRunner
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from statemachine import State, StateMachine


class _Silent:
    async def send(self, message: str) -> None:
        pass


@dataclass
class World:
    manager: ResourceManager
    store: InMemoryStore
    processor: MessageProcessor
    relay: ProcessFeed

    async def approved_invoice(self) -> int:
        m = self.manager
        invoice = await m.create("invoices", {"customer": "ACME", "amount": "120.50"})
        await m.transition("invoices", invoice.id, "submit")
        await m.documents.attach(
            "invoices",
            invoice.id,
            "signed_pdf",
            b"%PDF-1.7",
            filename="signada.pdf",
            content_type="application/pdf",
        )
        await m.transition("invoices", invoice.id, "approve")
        return invoice.id

    async def payments(self) -> list:
        return (await self.manager.list("invoice_payments", ListQuery(order_by=["id"]))).items

    async def deliver(self, event_type: str, payload: dict, **kw) -> Outcome:
        envelope = EventEnvelope(
            type=event_type, payload=payload, occurred_at=datetime.now(UTC), **kw
        )
        result = await self.processor.process(ReceivedMessage(envelope, message_id=envelope.id))
        return result.outcome


@pytest.fixture
def world() -> World:
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        event_delivery=OutboxDelivery(),
        audit=True,
    )
    processor = MessageProcessor(ProcessRunner(manager).router(), name="processes", manager=manager)
    relay = ProcessFeed(store.unit_of_work, processor)
    return World(manager, store, processor, relay)


async def test_an_approved_invoice_starts_the_process_and_a_payment_settles_it(world):
    invoice = await world.approved_invoice()
    assert await world.payments() == []  # not before the outbox is relayed
    await world.relay.run_once()
    [payment] = await world.payments()
    assert (payment.invoice_id, str(payment.amount), payment.status) == (
        invoice,
        "120.50",
        "awaiting",
    )
    assert await world.relay.run_once() == 1  # the process's own creation event
    assert len(await world.payments()) == 1

    outcome = await world.deliver("PaymentReceived", {"invoice_id": invoice, "amount": "120.50"})
    assert outcome is Outcome.ACK
    [payment] = await world.payments()
    assert payment.status == "settled"
    assert (await world.manager.get("invoices", invoice)).status == "paid"

    history = await world.manager.history("invoice_payments", payment.id)
    assert [(h.to_state, h.actor) for h in history] == [
        ("awaiting", "system:processes"),
        ("settled", "system:processes"),
    ]
    again = await world.deliver("PaymentReceived", {"invoice_id": invoice, "amount": "120.50"})
    assert again is Outcome.ACK  # a repeated notification changes nothing
    assert (await world.manager.get("invoices", invoice)).status == "paid"


async def test_exactly_once_per_message(world):
    invoice = await world.approved_invoice()
    await world.relay.run_once()
    envelope = EventEnvelope(
        type="PaymentReceived",
        payload={"invoice_id": invoice, "amount": "1"},
        occurred_at=datetime.now(UTC),
    )
    first = await world.processor.process(ReceivedMessage(envelope, message_id=envelope.id))
    second = await world.processor.process(ReceivedMessage(envelope, message_id=envelope.id))
    assert (first.outcome, second.outcome) == (Outcome.ACK, Outcome.DUPLICATE)


async def test_events_without_an_instance_or_with_bad_payloads(world):
    assert await world.deliver("PaymentReceived", {"invoice_id": 999, "amount": "1"}) is Outcome.ACK
    assert await world.payments() == []
    bad = await world.deliver("PaymentReceived", {"invoice_id": "x"})
    assert bad is Outcome.DEAD_LETTER  # MessageRejected: never retried
    assert await world.deliver("SomethingElse", {}) is Outcome.ACK  # no process listens


async def test_a_failing_step_rolls_back_everything(world, monkeypatch):
    invoice = await world.approved_invoice()
    await world.relay.run_once()
    # someone pays the invoice by hand first: the process's own pay transition fails
    await world.manager.transition("invoices", invoice, "pay")
    outcome = await world.deliver("PaymentReceived", {"invoice_id": invoice, "amount": "1"})
    assert outcome is Outcome.RETRY  # retried with backoff, then dead-lettered
    [payment] = await world.payments()
    assert payment.status == "awaiting"  # the process's receive rolled back with it


async def test_timeouts_and_late_payments(world):
    invoice = await world.approved_invoice()
    await world.relay.run_once()
    later = datetime.now(UTC) + timedelta(days=16)
    run = await TimerJob(
        world.manager, resources=["invoice_payments"], clock=lambda: later
    ).run_once()
    assert run.fired == 1
    [payment] = await world.payments()
    assert payment.status == "overdue"
    async with world.store.unit_of_work() as uow:
        pending = [r.envelope.type for r in await uow.outbox().pending(100)]
    assert "PaymentOverdue" in pending  # announced to other services
    await world.relay.run_once()
    late = await world.deliver("PaymentReceived", {"invoice_id": invoice, "amount": "1"})
    assert late is Outcome.ACK
    assert (await world.payments())[0].status == "settled"


async def test_relay_retries_failed_messages(world, monkeypatch):
    await world.approved_invoice()

    async def broken(step):
        raise RuntimeError("payments service down")

    on = ProcessRunner(world.manager).reactions()[0][1]  # the start on approval
    object.__setattr__(on, "handler", broken)
    try:
        settled = await world.relay.run_once()
        [failed] = await _pending(world)  # the others were settled
        assert settled == 3 and failed.attempts == 1
        assert failed.envelope.payload["transition"] == "approve"
    finally:
        from invoices.application.processes import start_payment

        object.__setattr__(on, "handler", start_payment)
    await world.relay.run_once()  # retried: now it works
    assert len(await world.payments()) == 1


async def _pending(world) -> list:
    async with world.store.unit_of_work() as uow:
        return await uow.outbox().pending(100)


def test_process_records_are_read_only_from_outside():
    definition = registry.get("invoice_payments")
    assert definition.exposed("list") and definition.exposed("get")
    assert not definition.exposed("create") and definition.enabled("create")
    assert definition.expose_transitions is False


# ----------------------------------------------------------- declarations


class Flow(StateMachine):
    open = State(initial=True)
    done = State(final=True)
    finish = open.to(done)


@dataclass
class Job:
    ref: int
    status: str | None = None
    id: int | None = None


class JobIn(BaseModel):
    ref: int


class JobOut(JobIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str | None = None


def _process(**attrs):
    base = {
        "name": "jobs",
        "model": Job,
        "schemas": {"create": JobIn, "read": JobOut},
        "operations": ["list", "get", "create"],
        "state_machine": Flow,
        "correlation": "ref",
        "reacts": [On("Started", lambda step: None, key="ref", start=True)],
    }
    return type("JobProcess", (Process,), {**base, **attrs})


def _runner(process):
    reg = ResourceRegistry()
    reg.register(process)
    return ProcessRunner(ResourceManager(reg, InMemoryStore.from_registry(reg).unit_of_work))


def test_declarations_are_checked():
    assert _runner(_process()).processes[0].name == "jobs"
    for attrs, message in (
        ({"correlation": None}, "declare correlation"),
        ({"correlation": "missing"}, "create must be enabled"),
        ({"operations": ["list", "get"], "internal_operations": ()}, "create must be enabled"),
        ({"operations": ["get", "create"]}, "list must be enabled"),
        ({"reacts": []}, "lists no events"),
        ({"reacts": ["Started"]}, "must hold On"),
    ):
        with pytest.raises(ConfigurationError, match=message):
            _runner(_process(**attrs))
