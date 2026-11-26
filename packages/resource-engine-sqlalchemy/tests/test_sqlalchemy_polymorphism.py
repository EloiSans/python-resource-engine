"""Base and subtype resources on joined tables (SQLite)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import EntityNotFound, VersionConflict
from resource_engine_sqlalchemy import extension_table, map_family, unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry
from statemachine import State, StateMachine


class RequestFlow(StateMachine):
    """Request flow."""

    open = State(initial=True)
    granted = State(final=True)
    grant = open.to(granted)


@dataclass
class Request:
    title: str
    type: str | None = None
    status: str | None = None
    version: int | None = None
    id: int | None = None


@dataclass
class Vacation(Request):
    days: int = 0


@dataclass
class Expense(Request):
    amount: Decimal = Decimal(0)
    iban: str | None = None


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    type: str
    status: str
    version: int


class VacationIn(BaseModel):
    title: str
    days: int


class VacationRead(RequestRead):
    days: int


class ExpenseIn(BaseModel):
    title: str
    amount: Decimal
    iban: str | None = None


class ExpenseRead(RequestRead):
    amount: Decimal
    iban: str | None = None


class RequestResource(Resource):
    name = "requests"
    model = Request
    schemas = {"read": RequestRead}
    operations = ["list", "get"]
    discriminator = "type"
    state_machine = RequestFlow
    version_field = "version"
    sortable = ["id"]


class VacationResource(Resource):
    name = "vacation_requests"
    extends = RequestResource
    identity = "vacation"
    model = Vacation
    schemas = {"create": VacationIn, "update": VacationIn, "read": VacationRead}
    operations = ["list", "get", "create", "update", "delete"]


class ExpenseResource(Resource):
    name = "expense_requests"
    extends = RequestResource
    identity = "expense"
    model = Expense
    schemas = {"create": ExpenseIn, "read": ExpenseRead}
    operations = ["list", "get", "create"]


metadata = MetaData()
requests = Table(
    "requests",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(32), nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
)
vacations = extension_table("vacation_requests", requests, Column("days", Integer, nullable=False))
expenses = extension_table(
    "expense_requests",
    requests,
    Column("amount", Numeric(12, 2), nullable=False),
    Column("iban", String(34)),
)

registry = ResourceRegistry()
registry.register(VacationResource)
registry.register(ExpenseResource)
map_family(
    orm_registry(metadata=metadata),
    registry,
    "requests",
    {"requests": requests, "vacation_requests": vacations, "expense_requests": expenses},
)


@pytest.fixture
async def setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'family.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow), engine
    await engine.dispose()


async def test_rows_in_base_and_extension_tables(setup):
    manager, engine = setup
    vacation = await manager.create("vacation_requests", {"title": "Summer", "days": 10})
    expense = await manager.create("expense_requests", {"title": "Taxi", "amount": "12.50"})
    assert (vacation.id, expense.id) == (1, 2)
    async with engine.connect() as conn:
        base = (await conn.execute(text("SELECT id, type FROM requests ORDER BY id"))).all()
        days = (await conn.execute(text("SELECT id, days FROM vacation_requests"))).all()
    assert [tuple(r) for r in base] == [(1, "vacation"), (2, "expense")]
    assert [tuple(r) for r in days] == [(1, 10)]

    every = await manager.list("requests", ListQuery(order_by=["id"]))
    assert [(type(r).__name__, r.id) for r in every.items] == [
        ("VacationRead", 1),
        ("ExpenseRead", 2),
    ]
    assert every.items[0].days == 10
    assert (await manager.list("expense_requests")).total == 1
    with pytest.raises(EntityNotFound):
        await manager.get("expense_requests", vacation.id)


async def test_versions_transitions_and_deletes(setup):
    manager, engine = setup
    vacation = await manager.create("vacation_requests", {"title": "Summer", "days": 10})
    granted = await manager.transition("requests", vacation.id, "grant", expected_version=1)
    assert (granted.status, granted.version) == ("granted", 2)
    with pytest.raises(VersionConflict):
        await manager.update(
            "vacation_requests", vacation.id, {"title": "x", "days": 1}, expected_version=1
        )
    updated = await manager.update(
        "vacation_requests", vacation.id, {"title": "Winter", "days": 3}, expected_version=2
    )
    assert (updated.title, updated.days, updated.version) == ("Winter", 3, 3)
    await manager.delete("vacation_requests", vacation.id, expected_version=3)
    async with engine.connect() as conn:
        left = [
            (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
            for t in ("requests", "vacation_requests")
        ]
    assert left == [0, 0]
