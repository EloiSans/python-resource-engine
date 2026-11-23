"""Transactional outbox table for SQLAlchemy."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from resource_engine.messaging import EventEnvelope
from resource_engine.ports.messaging import OutboxRecord
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TABLE = "resource_engine_outbox"


def outbox_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    """Declare the outbox table on your metadata (and create it with your migrations)."""
    return Table(
        name,
        metadata,
        Column("id", String(64), primary_key=True),
        Column("seq", Integer, nullable=False, autoincrement=True, index=True),
        Column("type", String(200), nullable=False),
        Column("resource", String(200), nullable=True),
        Column("message", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("published", Boolean, nullable=False, default=False),
        Column("published_at", DateTime(timezone=True), nullable=True),
        Column("attempts", Integer, nullable=False, default=0),
        Column("last_error", Text, nullable=True),
        Index(f"ix_{name}_pending", "published", "seq"),
    )


class SqlAlchemyOutbox:
    def __init__(self, session: AsyncSession, table: Table) -> None:
        self.session = session
        self.table = table

    async def add(self, envelopes: Sequence[EventEnvelope]) -> None:
        if not envelopes:
            return
        now = datetime.now(UTC)
        # seq keeps insertion order across a batch even with equal timestamps
        start = await self.session.scalar(
            select(self.table.c.seq).order_by(self.table.c.seq.desc()).limit(1)
        )
        start = (start or 0) + 1
        await self.session.execute(
            self.table.insert(),
            [
                {
                    "id": env.id,
                    "seq": start + offset,
                    "type": env.type,
                    "resource": env.resource,
                    "message": env.to_json(),
                    "created_at": now,
                    "published": False,
                    "attempts": 0,
                }
                for offset, env in enumerate(envelopes)
            ],
        )

    async def pending(self, limit: int) -> list[OutboxRecord]:
        stmt = (
            select(self.table)
            .where(self.table.c.published.is_(False))
            .order_by(self.table.c.seq)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        return [
            OutboxRecord(
                envelope=EventEnvelope.from_dict(json.loads(row["message"])),
                attempts=row["attempts"],
                last_error=row["last_error"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def mark_published(self, ids: Sequence[str]) -> None:
        if ids:
            await self.session.execute(
                update(self.table)
                .where(self.table.c.id.in_(list(ids)))
                .values(published=True, published_at=datetime.now(UTC))
            )

    async def mark_failed(self, ids: Sequence[str], error: str) -> None:
        if ids:
            await self.session.execute(
                update(self.table)
                .where(self.table.c.id.in_(list(ids)))
                .values(attempts=self.table.c.attempts + 1, last_error=error[:2000])
            )
