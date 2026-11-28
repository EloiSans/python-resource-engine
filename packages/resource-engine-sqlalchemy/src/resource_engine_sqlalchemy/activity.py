"""The activity log in tables (``ActivityLog``), shared by the worker and the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from resource_engine.ports.activity import DeliveryRecord
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_PREFIX = "resource_engine_activity"


@dataclass(frozen=True)
class ActivityTables:
    deliveries: Table

    def all(self) -> list[Table]:
        return [self.deliveries]


def activity_tables(metadata: MetaData, prefix: str = DEFAULT_PREFIX) -> ActivityTables:
    deliveries = Table(
        f"{prefix}_deliveries",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("consumer", String(200), nullable=False),
        Column("message_id", String(200), nullable=False),
        Column("event_type", String(200), nullable=False),
        Column("outcome", String(40), nullable=False),
        Column("seconds", Float, nullable=False),
        Column("error", String(1000)),
        Column("at", DateTime(timezone=True), nullable=False),
        Index(f"ix_{prefix}_deliveries_message", "message_id"),
        Index(f"ix_{prefix}_deliveries_at", "at"),
    )
    return ActivityTables(deliveries)


class SqlAlchemyActivityLog:
    """``ActivityLog`` in its own short transactions."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], tables: ActivityTables
    ) -> None:
        self.session_factory = session_factory
        self.tables = tables

    async def record_delivery(self, record: DeliveryRecord) -> None:
        async with self.session_factory() as session:
            await session.execute(
                insert(self.tables.deliveries).values(
                    id=record.id,
                    consumer=record.consumer[:200],
                    message_id=record.message_id[:200],
                    event_type=record.event_type[:200],
                    outcome=record.outcome,
                    seconds=record.seconds,
                    error=record.error[:1000] if record.error else None,
                    at=record.at,
                )
            )
            await session.commit()

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        t = self.tables.deliveries
        async with self.session_factory() as session:
            rows = await session.execute(
                select(t).where(t.c.message_id == message_id).order_by(t.c.at, t.c.id)
            )
            return [
                DeliveryRecord(
                    consumer=r.consumer,
                    message_id=r.message_id,
                    event_type=r.event_type,
                    outcome=r.outcome,
                    seconds=r.seconds,
                    error=r.error,
                    at=_aware(r.at),
                    id=r.id,
                )
                for r in rows
            ]

    async def purge(self, before: datetime, limit: int) -> int:
        t = self.tables.deliveries
        async with self.session_factory() as session:
            ids = [
                row[0]
                for row in await session.execute(
                    select(t.c.id).where(t.c.at < before).order_by(t.c.at).limit(limit)
                )
            ]
            if ids:
                await session.execute(delete(t).where(t.c.id.in_(ids)))
                await session.commit()
            return len(ids)


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["ActivityTables", "SqlAlchemyActivityLog", "activity_tables"]
