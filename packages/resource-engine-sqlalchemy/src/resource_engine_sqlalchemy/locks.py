"""Lease table for ``LockProvider``: one row per lock name."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_TABLE = "resource_engine_locks"


def locks_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    """Declare the lease table on your metadata (and create it with your migrations)."""
    return Table(
        name,
        metadata,
        Column("name", String(200), primary_key=True),
        Column("owner", String(200), nullable=False),
        Column("expires_at", DateTime(timezone=True), nullable=False),
    )


class SqlAlchemyLocks:
    """``LockProvider`` on a lease table, in its own short transactions.

    Taking a lease is one conditional ``UPDATE`` (free, expired or ours) and,
    when no row exists yet, an ``INSERT`` guarded by the primary key, so two
    workers never both win. Works on any database SQLAlchemy supports.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], table: Table) -> None:
        self.session_factory = session_factory
        self.table = table

    async def acquire(self, name: str, owner: str, ttl: timedelta) -> bool:
        now = datetime.now(UTC)
        t = self.table
        async with self.session_factory() as session:
            result = await session.execute(
                update(t)
                .where(t.c.name == name, (t.c.expires_at < now) | (t.c.owner == owner))
                .values(owner=owner, expires_at=now + ttl)
            )
            if result.rowcount == 1:
                await session.commit()
                return True
            try:
                await session.execute(
                    insert(t).values(name=name, owner=owner, expires_at=now + ttl)
                )
                await session.commit()
            except IntegrityError:  # held by someone else
                await session.rollback()
                return False
            return True

    async def release(self, name: str, owner: str) -> None:
        t = self.table
        async with self.session_factory() as session:
            await session.execute(delete(t).where(t.c.name == name, t.c.owner == owner))
            await session.commit()
