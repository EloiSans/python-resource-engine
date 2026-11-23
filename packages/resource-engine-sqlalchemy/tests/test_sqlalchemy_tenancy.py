"""The tenant filter is applied in SQL: pages and totals are per tenant."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import system_principal
from resource_engine.errors import EntityNotFound
from resource_engine.ports.identity import Principal
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Lead:
    name: str
    org: str | None = None
    id: int | None = None


class LeadIn(BaseModel):
    name: str


class LeadOut(LeadIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    org: str


class LeadResource(Resource):
    model = Lead
    schemas = {"create": LeadIn, "read": LeadOut}
    operations = ["list", "get", "create"]
    sortable = ["id"]
    tenant_field = "org"


metadata = MetaData()
mapper = orm_registry(metadata=metadata)
mapper.map_imperatively(
    Lead,
    Table(
        "leads",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("org", String(20), nullable=False, index=True),
        Column("name", String(50)),
    ),
)


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tenancy.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(LeadResource)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow)
    await engine.dispose()


async def test_tenant_filter_in_sql(manager):
    job = system_principal("seed")
    for org, n in (("acme", 3), ("globex", 2)):
        for i in range(n):
            await manager.create("leads", {"name": f"{org}-{i}"}, actor=job, tenant=org)
    ana = Principal(subject="ana", issuer="idp", tenant="globex")
    page = await manager.list("leads", ListQuery(order_by=["id"], limit=1), actor=ana)
    assert page.total == 2 and page.items[0].name == "globex-0"
    with pytest.raises(EntityNotFound):
        await manager.get("leads", 1, actor=ana)
    assert (await manager.list("leads", actor=job)).total == 5
