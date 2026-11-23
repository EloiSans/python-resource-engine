"""Against a running OPA serving docker/opa/*.rego."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import EngineAuthorizer, system_principal
from resource_engine.errors import (
    ConditionNotSupported,
    EntityNotFound,
    PermissionDenied,
    RemoteCallFailed,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing.contracts import AuthorizerContract
from resource_engine_opa import OpaEngine
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy import JSON, Column, Integer, MetaData, Numeric, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry

pytestmark = pytest.mark.emulator("opa")


class TestOpaContract(AuthorizerContract):
    @pytest.fixture
    def contract_engine(self, opa_engine):
        return opa_engine


@dataclass
class Invoice:
    owner: str
    amount: Decimal
    status: str = "draft"
    tags: list[str] | None = None
    id: int | None = None


class InvoiceIn(BaseModel):
    owner: str
    amount: Decimal


class InvoiceOut(InvoiceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class InvoiceResource(Resource):
    model = Invoice
    schemas = {"create": InvoiceIn, "read": InvoiceOut}
    operations = ["list", "get", "create"]


metadata = MetaData()
orm_registry(metadata=metadata).map_imperatively(
    Invoice,
    Table(
        "invoices",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("owner", String(50)),
        Column("amount", Numeric(10, 2)),
        Column("status", String(10)),
        Column("tags", JSON),
    ),
)

ADA = Principal(subject="ada", issuer="idp", roles=frozenset({"clerk"}))
AUDITOR = Principal(subject="aud", issuer="idp", roles=frozenset({"auditor"}))


@pytest.fixture
async def manager(tmp_path, opa_engine):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'opa.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow, authorizer=EngineAuthorizer(opa_engine))
    await engine.dispose()


async def test_row_filter_in_sql_and_input_conditions(manager):
    job = system_principal()
    for owner, amount in ((ADA.id, "500"), ("other", "500"), ("other", "5"), (ADA.id, "1")):
        await manager.create("invoices", {"owner": owner, "amount": amount}, actor=job)
    async with manager.uow_factory() as uow:
        repo = uow.repository("invoices")
        void = await repo.get(4)
        void.status = "void"
        await repo.update(void)
        await uow.commit()
    page = await manager.list("invoices", actor=ADA)
    assert sorted(i.id for i in page.items) == [1, 3] and page.total == 2
    with pytest.raises(EntityNotFound):
        await manager.get("invoices", 4, actor=ADA)
    assert (await manager.create("invoices", {"owner": ADA.id, "amount": 999}, actor=ADA)).id
    with pytest.raises(PermissionDenied):
        await manager.create("invoices", {"owner": ADA.id, "amount": 1001}, actor=ADA)


async def test_rules_outside_the_fragment_fail_closed(manager):
    with pytest.raises(ConditionNotSupported, match="row filter"):
        await manager.list("invoices", actor=AUDITOR)


async def test_unreachable_opa_is_a_remote_failure():
    engine = OpaEngine("http://127.0.0.1:9")
    authz = EngineAuthorizer(engine)
    with pytest.raises(RemoteCallFailed):
        await authz.check(ADA, "invoices:get", resource=Invoice(ADA.id, Decimal(1), id=1))
