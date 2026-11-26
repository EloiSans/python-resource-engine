"""View resources: aggregate views computed from their source, custom views,
and the rules that keep them read-only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from invoices.bootstrap import registry, tenant_registry
from pydantic import BaseModel, ConfigDict
from resource_engine import (
    Aggregate,
    Count,
    ListQuery,
    Resource,
    ResourceManager,
    ResourceRegistry,
    Sum,
)
from resource_engine.application.relations import expansions
from resource_engine.errors import ConfigurationError, OperationNotEnabled
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def manager() -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
    )


async def _invoice(manager, customer_id, amount, *transitions):
    invoice = await manager.create(
        "invoices", {"customer": "x", "amount": amount, "customer_id": customer_id}
    )
    for event in transitions:
        data = {"reason": "Duplicada"} if event == "reject" else None
        await manager.transition("invoices", invoice.id, event, data)
    return invoice


async def test_balances_follow_the_invoices(manager):
    acme = await manager.create("customers", {"name": "ACME", "tax_id": "B111"})
    globex = await manager.create("customers", {"name": "Globex", "tax_id": "B222"})
    await _invoice(manager, acme.id, "100", "submit")  # pending: outstanding
    await _invoice(manager, acme.id, "50")  # draft: only in the total
    await _invoice(manager, globex.id, "30", "submit", "reject")
    await _invoice(manager, None, "999")  # no customer: not in any row

    page = await manager.list("customer_balances", ListQuery(order_by=["-total"]))
    rows = [(b.customer_id, b.invoices, b.total, b.outstanding, b.paid) for b in page.items]
    assert rows == [
        (acme.id, 2, Decimal(150), Decimal(100), Decimal(0)),
        (globex.id, 1, Decimal(30), Decimal(0), Decimal(0)),
    ]
    one = await manager.get("customer_balances", acme.id, expand=["customer"])
    assert one.total == Decimal(150) and expansions(one)["customer"].name == "ACME"
    filtered = await manager.list(
        "customer_balances", ListQuery(filters={"customer_id": globex.id})
    )
    assert filtered.total == 1
    customer = await manager.get("customers", acme.id, expand=["balance"])
    assert [b.total for b in expansions(customer)["balance"].items] == [Decimal(150)]


async def test_views_are_read_only_and_never_block_deletes(manager):
    with pytest.raises(OperationNotEnabled):
        await manager.create("customer_balances", {"customer_id": 1})
    lonely = await manager.create("customers", {"name": "Solo", "tax_id": "B333"})
    await manager.delete("customers", lonely.id)  # the view's reference is not a dependent


def test_tenant_views_group_by_the_tenant():
    view = tenant_registry.get("customer_balances")
    assert view.tenant_field == "organization"
    assert "organization" in view.view.group_by


@dataclass
class Line:
    order: int
    qty: int
    id: int | None = None


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order: int
    qty: int


class LineIn(BaseModel):
    order: int
    qty: int


class LineResource(Resource):
    name = "lines"
    model = Line
    schemas = {"create": LineIn, "read": LineRead}
    operations = ["create", "list"]


@dataclass
class OrderSize:
    order: int
    lines: int = 0
    qty: int = 0


class OrderSizeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order: int
    lines: int
    qty: int


def _view(**attrs):
    attrs = {
        "name": "order_sizes",
        "model": OrderSize,
        "schemas": {"read": OrderSizeRead},
        "operations": ["list", "get"],
        "id_field": "order",
        "view": Aggregate(
            "lines", group_by=["order"], values={"lines": Count(), "qty": Sum("qty")}
        ),
        **attrs,
    }
    return type("OrderSizeResource", (Resource,), attrs)


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        ({"operations": ["list", "delete"]}, "only lists and reads; remove delete"),
        ({"version_field": "lines"}, "remove a version field"),
        ({"view": Aggregate("lines", group_by=["order"], values={"lines": Count()})}, "differ"),
        ({"id_field": "qty"}, "must be one of group_by"),
        (
            {
                "view": Aggregate(
                    "lines", group_by=["order"], values={"lines": Count(), "qty": Sum()}
                )
            },
            "needs a source field",
        ),
        ({"view": "lines"}, "must be an Aggregate"),
    ],
)
def test_view_rules(attrs, message):
    with pytest.raises(ConfigurationError, match=message):
        _view(**attrs).definition()


def test_sources_are_checked_with_the_registry():
    bad = _view(
        view=Aggregate("lines", group_by=["order"], values={"lines": Count(), "qty": Sum("weight")})
    )
    reg = ResourceRegistry()
    reg.register(LineResource)
    reg.register(bad)
    with pytest.raises(ConfigurationError, match=r"\['weight'\] are not in lines"):
        ResourceManager(reg, InMemoryStore.from_registry(reg).unit_of_work)


async def test_custom_views_in_memory():
    custom = _view(view=True)
    reg = ResourceRegistry()
    reg.register(LineResource)
    reg.register(custom)
    with pytest.raises(ValueError, match="custom view"):
        InMemoryStore.from_registry(reg)

    def sizes(read):
        orders: dict[int, dict] = {}
        for line in read("lines"):
            row = orders.setdefault(line.order, {"order": line.order, "lines": 0, "qty": 0})
            row["lines"] += 1
            row["qty"] += line.qty * 2  # anything Python can compute
        return list(orders.values())

    store = InMemoryStore.from_registry(reg, views={"order_sizes": sizes})
    manager = ResourceManager(reg, store.unit_of_work)
    await manager.create("lines", {"order": 7, "qty": 3})
    await manager.create("lines", {"order": 7, "qty": 1})
    assert (await manager.get("order_sizes", 7)).qty == 8
