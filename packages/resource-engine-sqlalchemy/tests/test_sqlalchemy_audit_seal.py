"""Sealing and retention of the audit trail on SQLite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.audit import AuditJanitor, AuditSealer
from resource_engine_sqlalchemy import (
    audit_seal_table,
    audit_table,
    map_audit,
    unit_of_work_factory,
)
from sqlalchemy import Column, Integer, MetaData, String, Table, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Memo:
    text: str
    id: int | None = None


class MemoIn(BaseModel):
    text: str


class MemoOut(MemoIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MemoResource(Resource):
    model = Memo
    schemas = {"create": MemoIn, "read": MemoOut}
    operations = ["get", "create"]


metadata = MetaData()
mapper = orm_registry(metadata=metadata)
mapper.map_imperatively(
    Memo,
    Table(
        "memos",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("text", String(50)),
    ),
)
entries_table = audit_table(metadata, "seal_test_audit")
map_audit(mapper, entries_table, audit_seal_table(metadata, "seal_test_seals"))


@pytest.fixture
async def setup(tmp_path):
    from resource_engine.audit import AuditEntry
    from sqlalchemy import inspect

    table = inspect(AuditEntry).local_table  # first mapping in this process wins
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'seal.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.run_sync(table.metadata.create_all)
    registry = ResourceRegistry()
    registry.register(MemoResource)
    registry.register_audit(seals=True)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow, audit=True), uow, engine, table
    await engine.dispose()


async def test_seal_verify_tamper_and_purge(setup):
    manager, uow, engine, table = setup
    for i in range(5):
        await manager.create("memos", {"text": f"memo {i}"})
    sealer = AuditSealer(uow, key=b"secret", batch_size=2)
    seals = await sealer.seal_pending(datetime.now(UTC) + timedelta(hours=1))
    assert [s.entries for s in seals] == [2, 2, 1]
    report = await sealer.verify()
    assert report.ok, report.problems
    assert report.entries == 5

    async with engine.begin() as conn:
        await conn.execute(update(table).where(table.c.seal == 2).values(actor="mallory"))
    assert [(p.sequence, p.kind) for p in (await sealer.verify()).problems] == [
        (2, "entries_changed")
    ]
    async with engine.begin() as conn:
        await conn.execute(update(table).where(table.c.seal == 2).values(actor=None))
    assert (await sealer.verify()).ok

    janitor = AuditJanitor(uow, retention=timedelta(days=30), sealer=sealer)
    stats = await janitor.run_once(datetime.now(UTC) + timedelta(days=31))
    assert (stats.purged, stats.purged_seals) == (5, 3)
    report = await sealer.verify()
    assert report.ok and report.purged_seals == 3 and report.entries == 0


async def test_retention_rules_compile_to_sql(setup):
    from resource_engine.audit import Retention

    manager, uow, engine, table = setup
    for i in range(3):
        await manager.create("memos", {"text": f"memo {i}"})
    async with engine.begin() as conn:  # one refused attempt, among the changes
        await conn.execute(update(table).where(table.c.entity_id == "1").values(outcome="denied"))
    later = datetime.now(UTC) + timedelta(days=10)
    rules = [Retention(timedelta(days=7), outcome="denied"), Retention(None, resource="memos")]
    stats = await AuditJanitor(uow, retention=timedelta(days=1), rules=rules).run_once(later)
    assert stats.purged == 1  # the denied one; other memo entries are kept forever
    sealer = AuditSealer(uow, batch_size=1)
    janitor = AuditJanitor(
        uow, sealer=sealer, rules=[Retention(timedelta(days=7), resource="memos")]
    )
    stats = await janitor.run_once(later)
    assert stats.seals == 2 and stats.purged_seals == 2 and (await sealer.verify()).ok


async def test_suppression_list_on_sql(tmp_path):
    from resource_engine_sqlalchemy import SqlAlchemySuppressionList, engine_tables

    meta = MetaData()
    tables = engine_tables(meta, suppressions=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
    store = SqlAlchemySuppressionList(async_sessionmaker(engine), tables.suppressions)
    await store.suppress("Ann <A@x.com>", "bounce")
    await store.suppress("a@x.com", "complaint")  # again: the reason is refreshed
    assert await store.suppressed(["a@X.com", "b@x.com"]) == {"a@x.com"}
    await store.release("a@x.com")
    assert await store.suppressed(["a@x.com"]) == set()
    await engine.dispose()
