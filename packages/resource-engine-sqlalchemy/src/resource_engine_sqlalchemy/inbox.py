"""Inbox table for idempotent consumers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TABLE = "resource_engine_inbox"


def inbox_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    """Declare the inbox table on your metadata (and create it with your migrations).

    Rows older than the broker's maximum redelivery window can go:
    ``MessagingJanitor`` deletes them.
    """
    return Table(
        name,
        metadata,
        Column("consumer", String(200), primary_key=True),
        Column("message_id", String(64), primary_key=True),
        Column("processed_at", DateTime(timezone=True), nullable=False, index=True),
    )


class SqlAlchemyInbox:
    def __init__(self, session: AsyncSession, table: Table) -> None:
        self.session = session
        self.table = table

    async def processed(self, consumer: str, message_ids: Sequence[str]) -> set[str]:
        if not message_ids:
            return set()
        stmt = select(self.table.c.message_id).where(
            self.table.c.consumer == consumer, self.table.c.message_id.in_(list(message_ids))
        )
        return set((await self.session.scalars(stmt)).all())

    async def mark_processed(self, consumer: str, message_ids: Sequence[str]) -> None:
        new = [i for i in message_ids if i not in await self.processed(consumer, message_ids)]
        if new:
            now = datetime.now(UTC)
            await self.session.execute(
                self.table.insert(),
                [{"consumer": consumer, "message_id": i, "processed_at": now} for i in new],
            )

    async def purge(self, before: datetime, limit: int) -> int:
        t = self.table
        keys = (
            await self.session.execute(
                select(t.c.consumer, t.c.message_id)
                .where(t.c.processed_at < before)
                .order_by(t.c.processed_at)
                .limit(limit)
            )
        ).all()
        if not keys:
            return 0
        await self.session.execute(
            delete(t).where(tuple_(t.c.consumer, t.c.message_id).in_([tuple(k) for k in keys]))
        )
        return len(keys)
