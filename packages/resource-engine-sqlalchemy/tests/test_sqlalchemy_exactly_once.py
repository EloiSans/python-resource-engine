"""Handler writes and the inbox mark commit together (SQLite)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.messaging import MessageProcessor, Outcome, RetryPolicy
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.testing.contracts import contract_envelope
from resource_engine_sqlalchemy import inbox_table, unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Receipt:
    message: str
    id: int | None = None


class ReceiptIn(BaseModel):
    message: str


class ReceiptOut(ReceiptIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ReceiptResource(Resource):
    model = Receipt
    schemas = {"create": ReceiptIn, "read": ReceiptOut}
    operations = ["list", "create"]


metadata = MetaData()
inbox = inbox_table(metadata)
orm_registry(metadata=metadata).map_imperatively(
    Receipt,
    Table(
        "receipts",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("message", String(64)),
    ),
)


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'once.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(ReceiptResource)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    uow = unit_of_work_factory(sessions, registry, inbox_table=inbox)
    yield ResourceManager(registry, uow)
    await engine.dispose()


def received(envelope, n: int = 1) -> ReceivedMessage:
    return ReceivedMessage(message_id=envelope.id, envelope=envelope, delivery_count=n)


async def receipts(manager) -> int:
    return (await manager.list("receipts", ListQuery())).total


async def test_failure_leaves_neither_effect_nor_mark(manager):
    envelope = contract_envelope(1)
    fail = {"on": True}

    async def handler(env):
        await manager.create("receipts", {"message": env.id})
        if fail["on"]:
            raise ConnectionError("busy")

    processor = MessageProcessor(
        handler, name="billing", manager=manager, retry=RetryPolicy(base_delay=0)
    )
    assert (await processor.process(received(envelope))).outcome is Outcome.RETRY
    assert await receipts(manager) == 0
    fail["on"] = False
    assert (await processor.process(received(envelope, 2))).outcome is Outcome.ACK
    assert (await processor.process(received(envelope, 3))).outcome is Outcome.DUPLICATE
    assert await receipts(manager) == 1


async def test_racing_consumers_apply_a_message_once(manager):
    envelope = contract_envelope(1)
    started: list[int] = []
    second_started = asyncio.Event()

    async def handler(env):
        started.append(1)
        if len(started) == 1:
            await second_started.wait()  # both passed the inbox check
        else:
            second_started.set()
        await manager.create("receipts", {"message": env.id})

    processors = [
        MessageProcessor(handler, name="billing", manager=manager, retry=RetryPolicy(base_delay=0))
        for _ in range(2)
    ]
    results = await asyncio.gather(*(p.process(received(envelope)) for p in processors))
    assert sorted(r.outcome.value for r in results) == ["ack", "duplicate"]
    assert await receipts(manager) == 1
