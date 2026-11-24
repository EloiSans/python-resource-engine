"""Lease table on SQLite."""

from __future__ import annotations

import pytest
from resource_engine.testing.contracts import LockContract
from resource_engine_sqlalchemy import SqlAlchemyLocks, locks_table
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

metadata = MetaData()
table = locks_table(metadata)


class TestSqlAlchemyLocks(LockContract):
    @pytest.fixture
    async def lock_provider(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'locks.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield SqlAlchemyLocks(async_sessionmaker(engine, expire_on_commit=False), table)
        await engine.dispose()
