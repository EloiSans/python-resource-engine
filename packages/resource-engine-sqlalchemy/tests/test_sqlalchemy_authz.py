"""Policies stored in SQL and row-level filters compiled to SQL."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, SystemRole
from resource_engine.errors import EntityNotFound
from resource_engine.ports.identity import Principal
from resource_engine_sqlalchemy import authz_tables, map_authz, unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Project:
    name: str
    tenant: str
    budget: int
    id: int | None = None


class ProjectIn(BaseModel):
    name: str
    tenant: str
    budget: int


class ProjectOut(ProjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProjectResource(Resource):
    model = Project
    schemas = {"create": ProjectIn, "read": ProjectOut}
    operations = ["list", "get", "create"]
    sortable = ["id"]


metadata = MetaData()
mapper = orm_registry(metadata=metadata)
mapper.map_imperatively(
    Project,
    Table(
        "projects",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(50)),
        Column("tenant", String(10)),
        Column("budget", Integer),
    ),
)
map_authz(mapper, authz_tables(metadata))

ADMIN = Principal(subject="root", issuer="idp", roles=frozenset({"admins"}))
MEMBER = Principal(subject="m", issuer="idp", roles=frozenset({"members"}), tenant="acme")


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authz.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(ProjectResource)
    registry.register_authz()
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    config = AuthzConfig(
        system_roles=[SystemRole.admin()],
        role_source=IdpRoleSource({"admins": "admin", "members": "member"}),
    )
    yield ResourceManager(registry, uow, authorizer=PolicyAuthorizer(config, uow))
    await engine.dispose()


async def test_policies_in_sql_filter_rows_in_sql(manager):
    for name, tenant, budget in [("a", "acme", 10), ("b", "acme", 900), ("c", "other", 5)]:
        await manager.create(
            "projects", {"name": name, "tenant": tenant, "budget": budget}, actor=ADMIN
        )
    await manager.create(
        "authz_policies",
        {
            "name": "Members see small projects of their tenant",
            "effect": "allow",
            "subjects": {"roles": ["member"]},
            "actions": ["projects:list", "projects:get"],
            "condition": {
                "and": [
                    {"eq": [{"attr": "resource.tenant"}, {"attr": "subject.tenant"}]},
                    {"le": [{"attr": "resource.budget"}, 100]},
                ]
            },
        },
        actor=ADMIN,
    )
    page = await manager.list("projects", ListQuery(order_by=["id"]), actor=MEMBER)
    assert [p.name for p in page.items] == ["a"] and page.total == 1
    with pytest.raises(EntityNotFound):
        await manager.get("projects", 2, actor=MEMBER)
    stored = await manager.list("authz_policies", actor=ADMIN)
    assert stored.items[0].condition["and"][1]["le"][1] == 100
