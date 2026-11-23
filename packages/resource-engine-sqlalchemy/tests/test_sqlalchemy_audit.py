"""Audit entries are committed or rolled back with the change they describe."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine_sqlalchemy import audit_table, map_audit, unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Account:
    owner: str
    balance: int
    id: int | None = None


class AccountIn(BaseModel):
    owner: str
    balance: int


class AccountPatch(BaseModel):
    balance: int | None = None


class AccountOut(AccountIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


def refuse_negative(ctx):
    """Refuses negative balances after the change was applied."""
    if ctx.entity.balance < 0:
        raise ValueError("negative balance")


class AccountResource(Resource):
    model = Account
    schemas = {"create": AccountIn, "update": AccountPatch, "read": AccountOut}
    operations = ["list", "get", "create", "update"]
    hooks = {"after_update": refuse_negative}


metadata = MetaData()
mapper = orm_registry(metadata=metadata)
mapper.map_imperatively(
    Account,
    Table(
        "accounts",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("owner", String(50)),
        Column("balance", Integer),
    ),
)
map_audit(mapper, audit_table(metadata))


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(AccountResource)
    registry.register_audit()
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow, audit=True)
    await engine.dispose()


async def test_audit_entries_share_the_transaction(manager):
    account = await manager.create("accounts", {"owner": "ada", "balance": 10})
    await manager.update("accounts", account.id, {"balance": 5})
    with pytest.raises(ValueError):
        await manager.update("accounts", account.id, {"balance": -1})
    page = await manager.list(
        "audit_log", ListQuery(filters={"resource": "accounts"}, order_by=["at"])
    )
    assert [e.operation for e in page.items] == ["create", "update"]  # no rolled-back entry
    assert page.items[1].changes == {"balance": {"from": 10, "to": 5}}
    assert page.items[1].entity_id == str(account.id)
