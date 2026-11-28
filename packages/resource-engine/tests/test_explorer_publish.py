"""Publishing events by hand (the explorer's PUB): the catalogue, dry runs
against the event and its consumers, and publication through the outbox,
tagged and audited."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import (
    AuthzConfig,
    IdpRoleSource,
    PolicyAuthorizer,
    SystemRole,
    system_principal,
)
from resource_engine.domain.events import DomainEvent
from resource_engine.errors import EntityNotFound, PermissionDenied, ValidationFailed
from resource_engine.explorer import register_explorer
from resource_engine.messaging import EventRouter, OutboxDelivery
from resource_engine.messaging.cloudevents import from_cloudevent, to_cloudevent
from resource_engine.messaging.publishing import EventPublishing
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from resource_engine.versions import Deprecation, EventVersion


@dataclass(frozen=True, kw_only=True)
class BillPaidV1(DomainEvent):
    """A bill was paid (v1: the amount as text)."""

    bill_id: int
    amount: str


@dataclass(frozen=True, kw_only=True)
class BillPaid(DomainEvent):
    """A bill was paid in full."""

    schema_version = 2
    previous_versions = {
        1: EventVersion(BillPaidV1, lambda p: {**p, "amount": str(p["total"])},
                        deprecation=Deprecation(sunset=date(2027, 6, 30))),
    }  # fmt: skip
    bill_id: int
    total: Decimal


@dataclass
class Bill:
    customer: str
    id: int | None = None


class BillIn(BaseModel):
    customer: str


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "read": BillOut}
    operations = ["list", "get", "create"]
    events = [BillPaid]


class PaidPayload(BaseModel):
    bill_id: int
    total: Decimal
    currency: str  # this consumer wants a field the event doesn't have


billing = EventRouter(name="billing")


@billing.on(BillPaid, payload=PaidPayload, schema_version=2)
async def on_paid(payload: PaidPayload) -> None:  # pragma: no cover - not run
    pass


PUBLISHER = Principal(subject="p", issuer="i", roles=frozenset({"publishers"}))
ADMIN = Principal(subject="a", issuer="i", roles=frozenset({"admins"}))


def build(*, allow_publish: bool = True) -> tuple[EventPublishing, ResourceManager, InMemoryStore]:
    registry = ResourceRegistry()
    registry.register(BillResource)
    registry.register_authz()
    registry.register_audit()
    register_explorer(registry)
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        role_source=IdpRoleSource({"publishers": "publisher", "admins": "admin"}),
        system_roles=[
            SystemRole("publisher", grants=["events:publish:BillPaid"]),
            SystemRole("admin", grants=["*"], include_sensitive=True),
        ],
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(config, store.unit_of_work),
        event_delivery=OutboxDelivery(),
        audit=True,
    )
    return (
        EventPublishing(manager, consumers=[billing], allow_publish=allow_publish),
        manager,
        store,
    )


def test_the_catalogue_lists_each_version_with_an_example():
    publishing, *_ = build()
    [paid] = publishing.catalogue()
    assert paid.type == "BillPaid" and paid.resource == "bills"
    assert paid.summary == "A bill was paid in full."
    v1, v2 = paid.versions
    assert (v1.version, v1.deprecated, v2.version, v2.deprecated) == ("1", True, "2", False)
    assert set(v2.example) == {"bill_id", "total"} and "occurred_at" not in v2.schema["properties"]
    assert set(v1.example) == {"bill_id", "amount"}


async def test_a_dry_run_checks_the_event_and_its_consumers():
    publishing, *_ = build()
    ok = {"bill_id": 7, "total": "12.50"}
    problems = await publishing.validate("BillPaid", 2, ok, actor=PUBLISHER)
    assert [(p.check, p.path) for p in problems] == [("consumer:billing", "currency")]
    bad = await publishing.validate("BillPaid", 2, {"bill_id": "x"}, actor=PUBLISHER)
    assert {(p.check, p.path) for p in bad} >= {("event", "bill_id"), ("event", "total")}
    # v1 is a different shape, and billing only reads v2
    assert await publishing.validate("BillPaid", 1, {"bill_id": 7, "amount": "12.50"},
                                     actor=PUBLISHER) == []  # fmt: skip
    with pytest.raises(EntityNotFound):
        await publishing.validate("BillPaid", 9, ok, actor=PUBLISHER)
    with pytest.raises(EntityNotFound):
        await publishing.validate("ResourceCreated", None, ok, actor=PUBLISHER)  # engine event


async def test_only_an_explicit_grant_may_publish():
    publishing, *_ = build()
    with pytest.raises(PermissionDenied):  # "*" with include_sensitive is not enough
        await publishing.validate("BillPaid", 2, {}, actor=ADMIN)
    with pytest.raises(PermissionDenied):
        await publishing.validate("BillPaid", 2, {}, actor=None)


async def test_publishing_goes_through_the_outbox_tagged_and_audited():
    publishing, manager, store = build()
    envelopes = await publishing.publish(
        "BillPaid", 2, {"bill_id": 7, "total": "12.50"}, actor=PUBLISHER
    )
    current, older = envelopes  # the current version brings the older one it keeps
    assert (current.schema_version, older.schema_version) == ("2", "1")
    assert older.payload["amount"] == "12.50" and older.id == f"{current.id}.v1"
    assert current.tags == {"origin": "console", "publishedby": "oidc:p"}
    assert current.resource == "bills" and current.attributes()["origin"] == "console"
    async with store.unit_of_work() as uow:
        pending = await uow.outbox().pending(10)
    assert [r.envelope.id for r in pending] == [current.id, older.id]
    assert pending[0].envelope.tags == current.tags
    audit = await manager.list("audit_log", actor=system_principal("tests"))
    [entry] = [e for e in audit.items if e.action == "events:publish:BillPaid"]
    assert entry.outcome == "ok" and entry.details["envelopes"] == [current.id, older.id]

    [only_v1] = await publishing.publish("BillPaid", 1, {"bill_id": 7, "amount": "3"},
                                         actor=PUBLISHER)  # fmt: skip
    assert only_v1.schema_version == "1" and only_v1.payload == {"bill_id": 7, "amount": "3"}

    with pytest.raises(ValidationFailed) as refused:
        await publishing.publish("BillPaid", 2, {"bill_id": "x"}, actor=PUBLISHER)
    assert {d["path"] for d in refused.value.details} >= {"bill_id"}


async def test_publishing_is_off_unless_the_app_turns_it_on():
    publishing, manager, store = build(allow_publish=False)
    assert await publishing.validate("BillPaid", 2, {"bill_id": 1, "total": "1"},
                                     actor=PUBLISHER) != []  # a dry run still works  # fmt: skip
    with pytest.raises(PermissionDenied):
        await publishing.publish("BillPaid", 2, {"bill_id": 1, "total": "1"}, actor=PUBLISHER)
    async with store.unit_of_work() as uow:
        assert await uow.outbox().pending(10) == []


def test_tags_survive_the_wire():
    from resource_engine.messaging import EventEnvelope

    envelope = EventEnvelope.from_dict(
        {"id": "1", "type": "T", "occurred_at": "2026-11-28T10:00:00+00:00", "payload": {},
         "tags": {"origin": "console"}}
    )  # fmt: skip
    assert EventEnvelope.from_dict(envelope.to_dict()).tags == {"origin": "console"}
    event = to_cloudevent(envelope, source="/x")
    assert event["origin"] == "console" and from_cloudevent(event).tags == {"origin": "console"}
