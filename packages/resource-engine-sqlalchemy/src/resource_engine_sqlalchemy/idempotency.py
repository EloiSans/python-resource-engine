"""Idempotency records for SQLAlchemy: one row per caller and key."""

from __future__ import annotations

from datetime import datetime

from resource_engine.ports.idempotency import IdempotencyRecord
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TABLE = "resource_engine_idempotency"


def idempotency_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    """Declare the idempotency table. Its primary key ``(scope, key)`` is what
    stops two racing requests with one key from both committing."""
    return Table(
        name,
        metadata,
        Column("scope", String(300), primary_key=True),
        Column("key", String(255), primary_key=True),
        Column("fingerprint", String(64), nullable=False),
        Column("resource", String(200), nullable=False),
        Column("operation", String(50), nullable=False),
        Column("result", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    )


class SqlAlchemyIdempotency:
    def __init__(self, session: AsyncSession, table: Table) -> None:
        self.session = session
        self.table = table

    async def get(self, scope: str, key: str) -> IdempotencyRecord | None:
        t = self.table
        row = (
            (await self.session.execute(select(t).where(t.c.scope == scope, t.c.key == key)))
            .mappings()
            .first()
        )
        return IdempotencyRecord(**row) if row else None

    async def put(self, record: IdempotencyRecord) -> None:
        t = self.table
        # an expired record with the same key may still be there: replace it
        await self.session.execute(
            delete(t).where(
                t.c.scope == record.scope,
                t.c.key == record.key,
                t.c.expires_at <= record.created_at,
            )
        )
        await self.session.execute(
            t.insert().values(
                scope=record.scope,
                key=record.key,
                fingerprint=record.fingerprint,
                resource=record.resource,
                operation=record.operation,
                result=dict(record.result),
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )

    async def purge(self, before: datetime, limit: int) -> int:
        t = self.table
        keys = (
            await self.session.execute(
                select(t.c.scope, t.c.key).where(t.c.expires_at < before).limit(limit)
            )
        ).all()
        for scope, key in keys:
            await self.session.execute(delete(t).where(t.c.scope == scope, t.c.key == key))
        return len(keys)
