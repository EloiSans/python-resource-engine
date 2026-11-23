from __future__ import annotations

import pytest
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    ConditionContract,
    ContractItem,
    InboxContract,
    OutboxContract,
)
from resource_engine_sqlalchemy import (
    SqlAlchemyRepository,
    SqlAlchemyUnitOfWork,
    inbox_table,
    outbox_table,
)
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry

metadata = MetaData()
items = Table(
    "contract_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(50), nullable=False),
    Column("rank", Integer, nullable=False),
    Column("group", String(10), nullable=False),
)
outbox = outbox_table(metadata)
inbox = inbox_table(metadata)
orm_registry(metadata=metadata).map_imperatively(ContractItem, items)


@pytest.fixture
async def uow_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'contract.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repositories = {CONTRACT_RESOURCE: lambda s: SqlAlchemyRepository(s, ContractItem)}
    yield lambda: SqlAlchemyUnitOfWork(
        sessions, repositories, outbox_table=outbox, inbox_table=inbox
    )
    await engine.dispose()


class TestSqlAlchemyRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory


class TestSqlAlchemyOutbox(OutboxContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory


class TestSqlAlchemyInbox(InboxContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory
