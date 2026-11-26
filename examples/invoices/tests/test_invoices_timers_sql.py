"""Timed transitions over SQLAlchemy (SQLite): due records found in SQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from invoices.bootstrap import registry
from invoices.infrastructure.persistence import invoices_table, map_entities, metadata
from resource_engine import ResourceManager, TimerJob
from resource_engine.testing import InMemoryBlobStore
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _Silent:
    async def send(self, message: str) -> None:
        pass


async def test_pending_invoices_expire():
    map_entities()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    manager = ResourceManager(
        registry,
        unit_of_work_factory(sessions, registry),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
    )
    ids = []
    for _ in range(3):
        invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})
        await manager.transition("invoices", invoice.id, "submit")
        ids.append(invoice.id)
    async with engine.begin() as conn:  # two of them have waited 40 days
        await conn.execute(
            update(invoices_table)
            .where(invoices_table.c.id.in_(ids[:2]))
            .values(status_changed_at=datetime.now(UTC) - timedelta(days=40))
        )

    run = await TimerJob(manager, batch=1).run_once()
    assert (run.fired, run.due) == (1, {"invoices.reject": 2})  # batch: the rest next run
    assert (await TimerJob(manager).run_once()).fired == 1
    statuses = [(await manager.get("invoices", i)).status for i in ids]
    assert statuses == ["rejected", "rejected", "pending"]
    await engine.dispose()
