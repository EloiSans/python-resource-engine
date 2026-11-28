"""The activity log's tables on SQLite."""

from __future__ import annotations

import pytest
from resource_engine.testing.contracts import ActivityLogContract
from resource_engine_sqlalchemy import SqlAlchemyActivityLog, activity_tables, engine_tables
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

metadata = MetaData()
tables = activity_tables(metadata)


class TestSqlAlchemyActivityLog(ActivityLogContract):
    @pytest.fixture
    async def activity_log(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'activity.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield SqlAlchemyActivityLog(async_sessionmaker(engine, expire_on_commit=False), tables)
        await engine.dispose()


def test_engine_tables_declare_the_activity_log():
    declared = engine_tables(MetaData(), activity=True, names={"activity": "ops"})
    assert [t.name for t in declared.all()] == ["ops_deliveries"]
