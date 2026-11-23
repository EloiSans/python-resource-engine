from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import EngineAuthorizer, system_principal
from resource_engine.errors import ConditionNotSupported, EntityNotFound, PermissionDenied
from resource_engine.ports.identity import Principal
from resource_engine.testing.contracts import AuthorizerContract
from resource_engine_cedar import CedarEngine, residual_condition
from resource_engine_cedar.engine import encode
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry

CONTRACT_POLICIES = """
permit(principal, action in [Action::"contract_docs:get", Action::"contract_docs:list",
                             Action::"contract_docs:update"], resource)
when { resource.owner == principal.id };
permit(principal in Role::"writer", action == Action::"contract_docs:create", resource);
permit(principal in Role::"auditor", action == Action::"contract_docs:read:secret", resource);
"""


class TestCedarContract(AuthorizerContract):
    @pytest.fixture
    def contract_engine(self):
        return CedarEngine(CONTRACT_POLICIES)


# ------------------------------------------------------------------ translator

U = {"unknown": [{"Value": "resource"}]}


def attr(name, of=U):
    return {".": {"left": of, "attr": name}}


def policy(effect, body):
    scope = {"op": "All"}
    return {
        "effect": effect,
        "principal": scope,
        "action": scope,
        "resource": scope,
        "conditions": [{"kind": "when", "body": body}],
    }


def test_translator_covers_comparisons_sets_and_decimals():
    body = {
        "&&": {
            "left": {"lessThan": [attr("amount"), {"decimal": [{"Value": "100.5"}]}]},
            "right": {
                "||": {
                    "left": {"in": {"left": attr("status"), "right": {"Set": [{"Value": "a"}]}}},
                    "right": {"!": {"arg": {"has": {"left": U, "attr": "notes"}}}},
                }
            },
        }
    }
    deny = {"contains": {"left": attr("tags"), "right": {"Value": "x"}}}
    result = residual_condition(
        {"p0": policy("permit", body), "p1": policy("forbid", deny)},
        resource_type="Invoices",
        context={},
    )
    assert result == {
        "and": [
            {
                "and": [
                    {"lt": [{"attr": "resource.amount"}, Decimal("100.5")]},
                    {
                        "or": [
                            {"in": [{"attr": "resource.status"}, ["a"]]},
                            {"not": {"exists": {"attr": "resource.notes"}}},
                        ]
                    },
                ]
            },
            {"not": {"contains": [{"attr": "resource.tags"}, "x"]}},
        ]
    }


def test_translator_resolves_context_and_types():
    ctx = {"unknown": [{"Value": "context"}]}
    body = {
        "&&": {
            "left": {"is": {"left": U, "entity_type": "Invoices"}},
            "right": {
                "&&": {
                    "left": {"==": {"left": attr("operation", ctx), "right": {"Value": "get"}}},
                    "right": {
                        "<": {"left": attr("amount", attr("input", ctx)), "right": {"Value": 5}}
                    },
                }
            },
        }
    }
    assert residual_condition(
        {"p": policy("permit", body)}, resource_type="Invoices", context={"operation": "get"}
    ) == {"lt": [{"attr": "input.amount"}, 5]}
    assert (
        residual_condition({"p": policy("permit", body)}, resource_type="Other", context={})
        is False
    )


def test_unsupported_constructs_fail_closed():
    like = {"like": {"left": attr("name"), "pattern": [{"Literal": "a"}, "Wildcard"]}}
    with pytest.raises(ConditionNotSupported, match="row filter"):
        residual_condition({"p": policy("permit", like)}, resource_type="X", context={})


def test_values_are_encoded_for_cedar():
    assert encode({"a": Decimal("10.5"), "b": None, "c": {1, 2}, "d": 3}) == {
        "a": {"__extn": {"fn": "decimal", "arg": "10.5"}},
        "c": [1, 2],
        "d": 3,
    }
    assert encode(Decimal("7"))["__extn"]["arg"] == "7.0"


# ------------------------------------------------------------- end to end, SQL


@dataclass
class Invoice:
    owner: str
    amount: Decimal
    status: str = "draft"
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
    sortable = ["id"]


POLICIES = """
permit(principal in Role::"clerk", action == Action::"invoices:create", resource)
when { context.input.amount.lessThanOrEqual(decimal("1000.0")) };
permit(principal in Role::"clerk", action in [Action::"invoices:list", Action::"invoices:get"],
       resource is Invoices)
when { resource.owner == principal.id || resource.amount.lessThan(decimal("10.0")) };
forbid(principal, action, resource is Invoices) when { resource.status == "void" };
"""

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
    ),
)

ADA = Principal(subject="ada", issuer="idp", roles=frozenset({"clerk"}))
BEN = Principal(subject="ben", issuer="idp", roles=frozenset({"clerk"}))


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cedar.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow, authorizer=EngineAuthorizer(CedarEngine(POLICIES)))
    await engine.dispose()


async def test_row_filter_and_forbid_in_sql(manager):
    job = system_principal()
    for owner, amount in ((ADA.id, "500"), (BEN.id, "500"), (BEN.id, "5"), (ADA.id, "1")):
        await manager.create("invoices", {"owner": owner, "amount": amount}, actor=job)
    async with manager.uow_factory() as uow:  # void one of Ada's
        repo = uow.repository("invoices")
        void = await repo.get(4)
        void.status = "void"
        await repo.update(void)
        await uow.commit()
    page = await manager.list("invoices", actor=ADA)
    assert sorted(i.id for i in page.items) == [1, 3] and page.total == 2
    with pytest.raises(EntityNotFound):
        await manager.get("invoices", 2, actor=ADA)
    with pytest.raises(EntityNotFound):
        await manager.get("invoices", 4, actor=ADA)


async def test_input_conditions(manager):
    assert (await manager.create("invoices", {"owner": ADA.id, "amount": "999"}, actor=ADA)).id
    with pytest.raises(PermissionDenied):
        await manager.create("invoices", {"owner": ADA.id, "amount": "1001"}, actor=ADA)
