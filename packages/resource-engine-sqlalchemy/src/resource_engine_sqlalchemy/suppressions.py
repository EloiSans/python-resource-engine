"""The email suppression list in a table (``SuppressionList``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parseaddr

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_TABLE = "resource_engine_email_suppressions"


def suppressions_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    return Table(
        name,
        metadata,
        Column("address", String(320), primary_key=True),
        Column("reason", String(500), nullable=False, default=""),
        Column("suppressed_at", DateTime(timezone=True), nullable=False),
    )


class SqlAlchemySuppressionList:
    """``SuppressionList`` shared by every instance, in its own short transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], table: Table) -> None:
        self.session_factory = session_factory
        self.table = table

    @staticmethod
    def _key(address: str) -> str:
        return (parseaddr(address)[1] or address).lower()

    async def suppressed(self, addresses: Sequence[str]) -> set[str]:
        keys = {self._key(a) for a in addresses}
        if not keys:
            return set()
        async with self.session_factory() as session:
            rows = await session.execute(
                select(self.table.c.address).where(self.table.c.address.in_(keys))
            )
            return {row[0] for row in rows}

    async def suppress(self, address: str, reason: str) -> None:
        values = {"reason": reason[:500], "suppressed_at": datetime.now(UTC)}
        key = self._key(address)
        async with self.session_factory() as session:
            try:
                await session.execute(insert(self.table).values(address=key, **values))
                await session.commit()
            except IntegrityError:  # already there: refresh the reason
                await session.rollback()
                await session.execute(
                    update(self.table).where(self.table.c.address == key).values(**values)
                )
                await session.commit()

    async def release(self, address: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                delete(self.table).where(self.table.c.address == self._key(address))
            )
            await session.commit()


__all__ = ["SqlAlchemySuppressionList", "suppressions_table"]
