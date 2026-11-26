"""Timed transitions: declaration, the state clock and TimerJob."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from invoices.bootstrap import registry as invoices
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry, Timer, TimerJob
from resource_engine.errors import ConfigurationError
from resource_engine.jobs import JobRunner
from resource_engine.testing import InMemoryBlobStore, InMemoryLocks, InMemoryStore
from statemachine import State, StateMachine


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def manager() -> ResourceManager:
    store = InMemoryStore.from_registry(invoices)
    return ResourceManager(
        invoices,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        audit=True,
    )


async def _pending(manager) -> int:
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})
    await manager.transition("invoices", invoice.id, "submit")
    return invoice.id


async def test_the_state_clock(manager):
    before = datetime.now(UTC)
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})
    assert invoice.status_changed_at >= before
    updated = await manager.update("invoices", invoice.id, {"customer": "Other"})
    assert updated.status_changed_at == invoice.status_changed_at  # not a state change
    submitted = await manager.transition("invoices", invoice.id, "submit")
    assert submitted.status_changed_at > invoice.status_changed_at


async def test_due_records_are_transitioned(manager):
    old = await _pending(manager)
    fresh = await _pending(manager)
    draft = (await manager.create("invoices", {"customer": "x", "amount": "1"})).id
    later = datetime.now(UTC) + timedelta(days=30)

    early = await TimerJob(manager, resources=["invoices"]).run_once()
    assert early.fired == 0 and early.due == {"invoices.reject": 0}

    # only `old` waited 30 days: move its clock back
    record = await manager.get("invoices", old)
    async with manager.uow_factory() as uow:
        entity = await uow.repository("invoices").get(old)
        entity.status_changed_at = record.status_changed_at - timedelta(days=31)
        await uow.repository("invoices").update(entity)
        await uow.commit()

    run = await TimerJob(manager, resources=["invoices"]).run_once()
    assert (run.fired, run.skipped, run.failed) == (1, 0, 0)
    rejected = await manager.get("invoices", old)
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Sense resposta en 30 dies"
    assert (await manager.get("invoices", fresh)).status == "pending"
    assert (await manager.get("invoices", draft)).status == "draft"

    history = await manager.history("invoices", old)
    assert history[-1].event == "reject" and history[-1].actor == "system:timers"

    everything = await TimerJob(
        manager, resources=["invoices"], clock=lambda: later + timedelta(days=1)
    ).run_once()
    assert everything.fired == 1  # `fresh` too, a month later
    assert (
        await TimerJob(manager, resources=["invoices"], clock=lambda: later).run_once()
    ).fired == 0  # nothing left


async def test_a_record_that_moved_on_is_skipped(manager, monkeypatch):
    invoice = await _pending(manager)
    job = TimerJob(
        manager, resources=["invoices"], clock=lambda: datetime.now(UTC) + timedelta(days=31)
    )
    original = manager.list

    async def approve_meanwhile(*args, **kw):
        page = await original(*args, **kw)
        await manager.transition("invoices", invoice, "reject", {"reason": "Manual"})
        return page

    monkeypatch.setattr(manager, "list", approve_meanwhile)
    run = await job.run_once()
    assert (run.fired, run.skipped) == (0, 1)
    assert (await manager.get("invoices", invoice)).rejection_reason == "Manual"


async def test_failures_are_counted_and_retried(manager, monkeypatch, caplog):
    await _pending(manager)

    async def broken(*args, **kw):
        raise RuntimeError("database gone")

    monkeypatch.setattr(manager, "transition", broken)
    job = TimerJob(
        manager, resources=["invoices"], clock=lambda: datetime.now(UTC) + timedelta(days=31)
    )
    run = await job.run_once()
    assert run.failed == 1 and "database gone" in caplog.text


async def test_runs_as_a_job(manager):
    await _pending(manager)
    runner = JobRunner(locks=InMemoryLocks())
    runner.add(
        "timers",
        TimerJob(
            manager, resources=["invoices"], clock=lambda: datetime.now(UTC) + timedelta(days=31)
        ),
        every=timedelta(minutes=1),
        single=True,
    )
    [run] = await runner.run_once()
    assert run.outcome == "ok" and run.result.fired == 1


# ----------------------------------------------------------- declarations


class Flow(StateMachine):
    open = State(initial=True)
    closed = State(final=True)
    close = open.to(closed)


@dataclass
class Item:
    status: str | None = None
    opened_at: datetime | None = None
    id: int | None = None


class ItemIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    status: str | None = None
    opened_at: datetime | None = None


class Note(BaseModel):
    text: str


def _resource(**attrs):
    base = {
        "model": Item,
        "schemas": {"read": ItemIO},
        "operations": ["get"],
        "state_machine": Flow,
    }
    return type("ItemResource", (Resource,), {**base, **attrs})


def test_declarations_are_checked():
    ok = _resource(timers=[Timer("close", after=timedelta(hours=1), since="opened_at")])
    assert ok.definition().timers[0].states == ("open",)
    bad = [
        ({"timers": [Timer("open", after=timedelta(hours=1), since="opened_at")]}, "no transition"),
        ({"timers": [Timer("close", after=timedelta(0), since="opened_at")]}, "positive"),
        ({"timers": [Timer("close", after=timedelta(hours=1))]}, "no since field"),
        ({"timers": [Timer("close", after=timedelta(hours=1), since="nope")]}, "not part"),
        (
            {"timers": [Timer("close", after=timedelta(1), since="opened_at", states=["closed"])]},
            "cannot run from",
        ),
        (
            {"timers": [Timer("close", after=timedelta(1), since="opened_at", data={"x": 1})]},
            "takes no input",
        ),
        (
            {
                "transition_schemas": {"close": Note},
                "timers": [Timer("close", after=timedelta(1), since="opened_at")],
            },
            "does not match",
        ),
        ({"state_since_field": "nope"}, "not part of the read schema"),
        ({"state_machine": None, "timers": [Timer("close", after=timedelta(1))]}, "need a state"),
    ]
    for attrs, message in bad:
        with pytest.raises(ConfigurationError, match=message):
            _resource(**attrs).definition()


def test_since_field_is_not_writable():
    class ItemIn(BaseModel):
        opened_at: datetime | None = None

    resource = _resource(
        schemas={"read": ItemIO, "create": ItemIn},
        operations=["get", "create"],
        state_since_field="opened_at",
    )
    with pytest.raises(ConfigurationError, match="the engine sets it"):
        resource.definition()


def test_resources_filter():
    registry = ResourceRegistry()
    registry.register(_resource(state_since_field="opened_at"))
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    assert TimerJob(manager, resources=["invoices"]).timers() == []
    assert (
        len(
            TimerJob(
                ResourceManager(
                    invoices,
                    InMemoryStore.from_registry(invoices).unit_of_work,
                    blob_store=InMemoryBlobStore(),
                ),
                resources=["invoices"],
            ).timers()
        )
        == 1
    )
