"""The explorer's groundwork: live events filtered to what each subscriber may
see, engine actions granted only by name, audit entries outside operations,
and the activity log of consumer deliveries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.application.relations import dump_read
from resource_engine.authz import (
    AuthzConfig,
    IdpRoleSource,
    PolicyAuthorizer,
    SystemRole,
    system_principal,
)
from resource_engine.domain.events import DomainEvent
from resource_engine.errors import PermissionDenied
from resource_engine.explorer import allowed, publish_action, register_explorer, require
from resource_engine.messaging import (
    ActivityMessageObserver,
    EventEnvelope,
    EventHub,
    MessagingJanitor,
)
from resource_engine.messaging.delivery import TransportEventPublisher
from resource_engine.messaging.live import LiveEvents
from resource_engine.messaging.processing import Outcome, ProcessResult
from resource_engine.ports.activity import DeliveryRecord
from resource_engine.ports.identity import Principal
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.testing import InMemoryActivityLog, InMemoryStore
from resource_engine.testing.contracts import ActivityLogContract


@dataclass
class Bill:
    customer: str
    amount: Decimal
    org: str | None = None
    secret: str = "s"
    id: int | None = None


class BillIn(BaseModel):
    customer: str
    amount: Decimal
    secret: str = "s"


class BillPatch(BaseModel):
    amount: Decimal | None = None


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    org: str | None = None


@dataclass(frozen=True, kw_only=True)
class BillPaid(DomainEvent):
    bill_id: int


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "read": BillOut, "update": BillPatch}
    operations = ["list", "get", "create", "update", "delete"]
    tenant_field = "org"
    restricted_fields = ["secret"]
    events = [BillPaid]


READER = Principal(subject="r", issuer="i", roles=frozenset({"readers"}), tenant="acme")
OTHER_ORG = Principal(subject="o", issuer="i", roles=frozenset({"readers"}), tenant="globex")
WATCHER = Principal(subject="w", issuer="i", roles=frozenset({"watchers"}), tenant="acme")
ADMIN = Principal(subject="a", issuer="i", roles=frozenset({"admins"}), tenant="acme")
NOBODY = Principal(subject="n", issuer="i", tenant="acme")
SYSTEM = system_principal("tests")


def build(hub: EventHub, *, audit: bool = True) -> tuple[ResourceManager, InMemoryStore]:
    registry = ResourceRegistry()
    registry.register(BillResource)
    registry.register_authz()
    registry.register_audit()
    register_explorer(registry)
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        role_source=IdpRoleSource(
            {"readers": "reader", "watchers": "watcher", "admins": "admin"}
        ),  # fmt: skip
        system_roles=[
            SystemRole("reader", grants=["bills:get", "bills:list", "bills:create"]),
            SystemRole("watcher", grants=["bills:get", "bills:list", "events:read"]),
            SystemRole("admin", grants=["*"], include_sensitive=True),
        ],
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(config, store.unit_of_work),
        event_publisher=TransportEventPublisher(hub),
        audit=audit,
    )
    return manager, store


async def take(stream, count: int, timeout: float = 1.0) -> list:  # noqa: ANN001
    out = []

    async def run() -> None:
        async for item in stream:
            out.append(item)
            if len(out) == count:
                return

    try:
        await asyncio.wait_for(run(), timeout)
    except TimeoutError:
        pass
    finally:
        await stream.aclose()
    return out


async def started(hub: EventHub, before: int) -> None:
    for _ in range(50):
        if hub.subscribers > before:
            return
        await asyncio.sleep(0)


# ---------------------------------------------------------------- visibility


async def test_subscribers_see_events_about_records_they_can_read():
    hub = EventHub()
    manager, _ = build(hub)
    live = LiveEvents(manager, hub)
    reader = asyncio.create_task(take(live.stream(READER), 2))
    other = asyncio.create_task(take(live.stream(OTHER_ORG), 1, timeout=0.3))
    await started(hub, 1)
    bill = await manager.create("bills", {"customer": "ACME", "amount": "5"}, actor=READER)
    await manager.update("bills", bill.id, {"amount": "6"}, actor=SYSTEM, tenant="acme")
    events = await reader
    assert [e.envelope.type for e in events] == ["ResourceCreated", "ResourceUpdated"]
    first = events[0]
    assert first.resource == "bills" and first.entity_id == bill.id
    assert first.record.customer == "ACME"
    assert "secret" not in dump_read(first.record)  # as the reader may see it
    assert not first.payload_visible  # the payload needs events:read
    assert await other == []  # another tenant's record


async def test_deletions_only_for_records_the_stream_showed():
    hub = EventHub()
    manager, _ = build(hub)
    seen = await manager.create("bills", {"customer": "A", "amount": "1"}, actor=READER)
    hidden = await manager.create("bills", {"customer": "G", "amount": "1"}, actor=SYSTEM,
                                  tenant="globex")  # fmt: skip
    live = LiveEvents(manager, hub)
    task = asyncio.create_task(take(live.stream(READER, resource="bills"), 3, timeout=0.5))
    await started(hub, 0)
    await manager.update("bills", seen.id, {"amount": "2"}, actor=SYSTEM, tenant="acme")
    await asyncio.sleep(0.05)
    await manager.delete("bills", hidden.id, actor=SYSTEM, tenant="globex")
    await manager.delete("bills", seen.id, actor=SYSTEM, tenant="acme")
    events = [(e.envelope.type, e.entity_id) for e in await task]
    assert events == [("ResourceUpdated", seen.id), ("ResourceDeleted", seen.id)]


async def test_filters_and_events_about_no_record():
    hub = EventHub()
    manager, _ = build(hub)
    live = LiveEvents(manager, hub)
    watcher = asyncio.create_task(
        take(live.stream(WATCHER, types={"BillPaid"}, everything=True), 1)
    )
    reader = asyncio.create_task(take(live.stream(READER, types={"BillPaid"}), 1, timeout=0.3))
    await started(hub, 1)
    await manager.create("bills", {"customer": "A", "amount": "1"}, actor=READER)  # filtered out
    await hub.publish(
        [EventEnvelope(type="BillPaid", payload={"bill_id": 1}, occurred_at=datetime.now(UTC))]
    )
    [paid] = await watcher
    assert paid.resource is None and paid.payload_visible and paid.envelope.payload["bill_id"] == 1
    assert await reader == []  # about no record: only with events:read


# ------------------------------------------------------------ permissions


async def test_engine_actions_and_exact_publish_grants():
    manager, store = build(EventHub())
    catalog = manager.authorizer.catalog
    assert {"events:read", "jobs:read", "jobs:run", "events:publish:BillPaid"} <= set(
        catalog.actions
    )
    assert await allowed(manager, WATCHER, "events:read")
    assert not await allowed(manager, READER, "events:read")
    assert await allowed(manager, ADMIN, "jobs:run")  # "*" with include_sensitive
    # publishing is granted by name only: not even "*" with include_sensitive
    assert not await allowed(manager, ADMIN, "events:publish:BillPaid")
    assert publish_action("BillPaid").exact
    with pytest.raises(PermissionDenied):
        await require(manager, READER, "jobs:run")
    page = await manager.list("audit_log", actor=SYSTEM)
    entries = [e for e in page.items if e.action == "jobs:run"]
    assert entries and entries[0].outcome == "denied" and entries[0].resource == "explorer"


async def test_without_an_authorizer_only_an_open_explorer_allows():
    registry = ResourceRegistry()
    registry.register(BillResource)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    assert not await allowed(manager, None, "events:read")
    assert await allowed(manager, None, "events:read", open=True)


# ----------------------------------------------------------- activity log


async def test_deliveries_are_recorded_and_purged():
    log = InMemoryActivityLog()
    observer = ActivityMessageObserver(log)
    envelope = EventEnvelope(type="BillPaid", payload={}, occurred_at=datetime.now(UTC))
    message = ReceivedMessage(envelope)
    await observer("billing", message, ProcessResult(Outcome.ACK), 0.01)
    await observer("mailer", message, ProcessResult(Outcome.RETRY, "RE-E501: down"), 0.2)
    await observer("x", ReceivedMessage(None, error="bad json"), ProcessResult(Outcome.ACK), 0)
    found = await log.deliveries(envelope.id)
    assert [(d.consumer, d.outcome, d.error) for d in found] == [
        ("billing", "ack", None),
        ("mailer", "retry", "RE-E501: down"),
    ]
    old = DeliveryRecord("c", "m", "T", "ack", 0.0, at=datetime.now(UTC) - timedelta(days=30))
    await log.record_delivery(old)
    registry = ResourceRegistry()
    registry.register(BillResource)
    janitor = MessagingJanitor(
        InMemoryStore.from_registry(registry).unit_of_work,
        inbox_retention=None,
        outbox_retention=None,
        activity=log,
    )
    stats = await janitor.run_once()
    assert stats.activity == 1 and await log.deliveries("m") == []


class TestInMemoryActivityLog(ActivityLogContract):
    @pytest.fixture
    def activity_log(self):
        return InMemoryActivityLog()


async def test_events_read_sees_events_about_records_it_cannot_read():
    hub = EventHub()
    manager, _ = build(hub)
    live = LiveEvents(manager, hub)
    task = asyncio.create_task(take(live.stream(WATCHER, everything=True), 1))
    await started(hub, 0)
    other = await manager.create("bills", {"customer": "G", "amount": "1"}, actor=SYSTEM,
                                 tenant="globex")  # fmt: skip
    [event] = await task
    assert event.entity_id == other.id and event.record is None and event.payload_visible


async def test_the_outbox_also_feeds_live_subscribers():
    from resource_engine.messaging import OutboxDelivery

    hub = EventHub()
    registry = ResourceRegistry()
    registry.register(BillResource)
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, event_delivery=OutboxDelivery(live=hub))
    subscription = hub.subscribe()
    bill = await manager.create("bills", {"customer": "A", "amount": "1"}, actor=READER)
    live = await asyncio.wait_for(subscription.queue.get(), 1)
    async with store.unit_of_work() as uow:
        [stored] = await uow.outbox().pending(10)
    assert live.id == stored.envelope.id  # the same envelope: deliveries line up
    assert live.payload["entity_id"] == bill.id


async def test_a_resource_channel_includes_its_events_about_no_record():
    hub = EventHub()
    manager, _ = build(hub)
    live = LiveEvents(manager, hub)
    watcher = asyncio.create_task(take(live.stream(WATCHER, resource="bills", everything=True), 1))
    reader = asyncio.create_task(take(live.stream(READER, resource="bills"), 1, timeout=0.3))
    await started(hub, 1)
    await hub.publish([EventEnvelope(type="BillPaid", payload={"bill_id": 1}, resource="bills",
                                     occurred_at=datetime.now(UTC))])  # fmt: skip
    [paid] = await watcher
    assert paid.envelope.type == "BillPaid" and paid.resource is None
    assert await reader == []  # about no record: only with events:read
