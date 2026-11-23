"""Handler and inbox in one transaction; per-key concurrency; observers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import ConfigurationError, EntityNotFound
from resource_engine.messaging import (
    LoggingObserver,
    MessageConsumer,
    MessageProcessor,
    Outcome,
    RetryPolicy,
)
from resource_engine.testing import InMemoryQueue, InMemoryStore
from resource_engine.testing.contracts import contract_envelope


@dataclass
class Tally:
    name: str
    count: int = 0
    id: int | None = None


class TallyIn(BaseModel):
    name: str
    count: int = 0


class TallyOut(TallyIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TallyResource(Resource):
    name = "tallies"
    model = Tally
    schemas = {"create": TallyIn, "update": TallyIn, "read": TallyOut}
    operations = ["get", "create", "update"]


def build():
    registry = ResourceRegistry()
    registry.register(TallyResource)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work), store


async def processed(store, envelope, consumer="t"):
    async with store.unit_of_work() as uow:
        return await uow.inbox().processed(consumer, [envelope.id])


async def test_effects_and_mark_commit_together():
    manager, store = build()
    tally = await manager.create("tallies", {"name": "a"})
    attempts = {"n": 0}

    async def handler(env):
        current = await manager.get("tallies", tally.id)
        await manager.update("tallies", tally.id, {"name": "a", "count": current.count + 1})
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("downstream busy")  # after the write: rolled back

    queue = InMemoryQueue()
    envelope = contract_envelope(1)
    await queue.publish([envelope])
    processor = MessageProcessor(
        handler, name="t", manager=manager, retry=RetryPolicy(base_delay=0)
    )
    worker = MessageConsumer(queue, processor, wait_seconds=0.05)
    assert (await worker.run_once()).retried == 1
    assert (await manager.get("tallies", tally.id)).count == 0 and not await processed(
        store, envelope
    )
    assert (await worker.run_once()).acked == 1
    assert (await manager.get("tallies", tally.id)).count == 1 and await processed(store, envelope)
    await queue.publish([envelope])  # redelivered
    assert (await worker.run_once()).duplicates == 1
    assert (await manager.get("tallies", tally.id)).count == 1


async def test_doomed_transactions_are_classified_by_their_cause():
    manager, _ = build()

    async def handler(env):
        with contextlib.suppress(EntityNotFound):  # caught, but the transaction is doomed
            await manager.update("tallies", 99, {"name": "x"})

    processor = MessageProcessor(handler, name="t", manager=manager)
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1)])
    received = await queue.receive(1, 0)
    result = await processor.process(received[0])
    assert result.outcome is Outcome.RETRY and "EntityNotFound" in result.error


def test_manager_and_inbox_are_exclusive():
    manager, store = build()
    with pytest.raises(ConfigurationError):
        MessageProcessor(lambda e: None, name="t", manager=manager, inbox=store.unit_of_work)


async def test_keys_run_concurrently_and_keep_their_order():
    queue = InMemoryQueue()
    plan = [(1, "a"), (2, "b"), (3, "a"), (4, "b"), (5, None)]
    await queue.publish([contract_envelope(n, key=k) for n, k in plan])
    order: list[int] = []
    running, peak = 0, 0
    gate = asyncio.Event()

    async def handler(env):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        if running >= 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 1)  # only returns if another key overlaps
        order.append(env.payload["n"])
        running -= 1

    worker = MessageConsumer(
        queue, MessageProcessor(handler, name="t"), wait_seconds=0.05, concurrency=3
    )
    stats = await worker.run_once()
    assert stats.acked == 5 and peak >= 2
    assert order.index(1) < order.index(3) and order.index(2) < order.index(4)


async def test_blocked_keys_and_observers():
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(n, key=k) for n, k in [(1, "a"), (2, "b"), (3, "a")]])
    seen: list[tuple[int, str, float]] = []

    def handler(env):
        if env.payload["n"] == 1:
            raise ConnectionError("busy")

    def observer(consumer, message, result, seconds):
        seen.append((message.envelope.payload["n"], result.outcome.value, seconds))

    def broken(*args):
        raise RuntimeError("metrics down")

    processor = MessageProcessor(
        handler, name="t", retry=RetryPolicy(base_delay=0), observers=[broken, observer]
    )
    worker = MessageConsumer(queue, processor, wait_seconds=0.05, concurrency=2)
    stats = await worker.run_once()
    assert (stats.acked, stats.retried) == (1, 2)
    assert sorted((n, o) for n, o, _ in seen) == [(1, "retry"), (2, "ack"), (3, "retry")]
    assert [s for n, _, s in seen if n == 3] == [0.0]  # skipped behind the failure


async def test_logging_observer(caplog):
    caplog.set_level(logging.INFO, logger="resource_engine.messaging.metrics")
    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1, key="k")])
    processor = MessageProcessor(lambda e: None, name="billing", observers=[LoggingObserver()])
    await MessageConsumer(queue, processor, wait_seconds=0.05).run_once()
    assert "consumer=billing outcome=ack type=ContractEvent key=k" in caplog.text
