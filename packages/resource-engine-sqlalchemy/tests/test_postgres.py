"""The SQL adapter's contracts on PostgreSQL, and what only a real server
shows: SKIP LOCKED in the outbox and racing leases."""

from __future__ import annotations

import pytest
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    ConditionContract,
    ContractItem,
    InboxContract,
    LockContract,
    OutboxContract,
    contract_envelope,
)
from resource_engine_sqlalchemy import (
    SqlAlchemyLocks,
    SqlAlchemyRepository,
    SqlAlchemyUnitOfWork,
    inbox_table,
    locks_table,
    outbox_table,
)
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry

pytestmark = pytest.mark.emulator("postgres")

metadata = MetaData()
outbox = outbox_table(metadata, "test_outbox")
inbox = inbox_table(metadata, "test_inbox")
locks = locks_table(metadata, "test_locks")

if sa_inspect(ContractItem, raiseerr=False) is None:  # another test module may have mapped it
    orm_registry(metadata=metadata).map_imperatively(
        ContractItem,
        Table(
            "contract_items",
            metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("name", String(50), nullable=False),
            Column("rank", Integer, nullable=False),
            Column("group", String(10), nullable=False),
        ),
    )
ITEMS = sa_inspect(ContractItem).local_table


@pytest.fixture
async def sessions(postgres_database):
    engine = create_async_engine(postgres_database)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.run_sync(ITEMS.metadata.create_all, tables=[ITEMS])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def uow_factory(sessions):
    repositories = {CONTRACT_RESOURCE: lambda s: SqlAlchemyRepository(s, ContractItem)}
    return lambda: SqlAlchemyUnitOfWork(
        sessions, repositories, outbox_table=outbox, inbox_table=inbox
    )


class TestPostgresRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory


class TestPostgresOutbox(OutboxContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory


class TestPostgresInbox(InboxContract):
    @pytest.fixture
    def contract_uow_factory(self, uow_factory):
        return uow_factory


class TestPostgresLocks(LockContract):
    @pytest.fixture
    def lock_provider(self, sessions):
        return SqlAlchemyLocks(sessions, locks)


async def test_two_relays_take_different_records(uow_factory):
    envelopes = [contract_envelope(n) for n in range(4)]
    async with uow_factory() as uow:
        await uow.outbox().add(envelopes)
        await uow.commit()
    async with uow_factory() as first, uow_factory() as second:
        taken = [r.envelope.id for r in await first.outbox().pending(2)]  # locks two rows
        others = [r.envelope.id for r in await second.outbox().pending(10)]  # skips them
    assert taken == [e.id for e in envelopes[:2]]
    assert others == [e.id for e in envelopes[2:]]
