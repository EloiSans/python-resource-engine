from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from invoices.bootstrap import registry
from resource_engine import DomainError, DomainEvent, ResourceManager
from resource_engine.errors import NoHandler, OutboxNotSupported
from resource_engine.messaging import (
    EventEnvelope,
    EventRouter,
    EventSerializer,
    OutboxDelivery,
    OutboxRelay,
    TransportEventPublisher,
)
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingMessagePublisher
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    ConditionContract,
    OutboxContract,
)


class TestInMemoryRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self):
        return InMemoryStore({CONTRACT_RESOURCE: "id"}).unit_of_work


class TestInMemoryOutbox(OutboxContract):
    @pytest.fixture
    def contract_uow_factory(self):
        return InMemoryStore({CONTRACT_RESOURCE: "id"}).unit_of_work


@dataclass(frozen=True, kw_only=True)
class Paid(DomainEvent):
    invoice_id: int
    amount: Decimal


def test_envelope_json_round_trip():
    env = EventSerializer().to_envelope(Paid(invoice_id=3, amount=Decimal("9.90")))
    assert env.payload == {"invoice_id": 3, "amount": "9.90"}
    assert EventEnvelope.from_json(env.to_json()) == env
    assert env.attributes()["event_type"] == "Paid"


async def test_outbox_delivery_stores_events_with_the_transaction():
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    inv = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", inv.id, "submit")
    rows = store.outbox_records(published=False)
    assert [r["envelope"].type for r in rows] == ["ResourceCreated", "TransitionExecuted"]
    env = rows[1]["envelope"]
    assert env.key == f"invoices:{inv.id}" and env.payload["target"] == "pending"

    # a failed operation leaves nothing in the outbox
    with pytest.raises(DomainError):
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    assert len(store.outbox_records()) == 2


async def test_relay_publishes_and_retries_failures():
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    for _ in range(3):
        await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    transport = RecordingMessagePublisher()
    first_id = store.outbox_records()[0]["envelope"].id
    transport.fail_ids = {first_id}
    relay = OutboxRelay(store.unit_of_work, transport, batch_size=10)

    assert await relay.run_once() == 2
    (pending,) = store.outbox_records(published=False)
    assert pending["attempts"] == 1 and "rejected" in pending["last_error"]

    transport.fail_ids = set()
    assert await relay.run_once() == 1
    assert await relay.run_once() == 0
    assert len(transport.messages) == 3


async def test_relay_counts_broker_outage_as_attempt():
    class Down:
        async def publish(self, messages):
            raise ConnectionError("unreachable")

    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    assert await OutboxRelay(store.unit_of_work, Down()).run_once() == 0
    assert store.outbox_records()[0]["attempts"] == 1


async def test_outbox_requires_capable_unit_of_work():
    class PlainUoW:
        def __init__(self, inner):
            self.inner = inner

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            await self.inner.__aexit__(*exc)

        def repository(self, name):
            return self.inner.repository(name)

        async def commit(self):
            await self.inner.commit()

    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        lambda: PlainUoW(store.unit_of_work()),
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    with pytest.raises(OutboxNotSupported):
        await manager.create("invoices", {"customer": "ACME", "amount": "10"})


async def test_transport_event_publisher_for_direct_mode():
    transport = RecordingMessagePublisher()
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_publisher=TransportEventPublisher(transport),
        blob_store=InMemoryBlobStore(),
    )
    await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    assert [m.type for m in transport.messages] == ["ResourceCreated"]


async def test_router_dispatches_by_type():
    router = EventRouter()
    seen = []

    @router.on(Paid)
    async def handle(env):
        seen.append(env.payload["invoice_id"])

    await router.dispatch(EventSerializer().to_envelope(Paid(invoice_id=1, amount=Decimal(1))))
    assert seen == [1]
    with pytest.raises(NoHandler):
        await router.dispatch(EventEnvelope(type="Other", payload={}, occurred_at=env_time()))
    await EventRouter(ignore_unknown=True).dispatch(
        EventEnvelope(type="Other", payload={}, occurred_at=env_time())
    )


def env_time():
    from datetime import UTC, datetime

    return datetime.now(UTC)
