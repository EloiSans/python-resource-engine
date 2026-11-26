"""References between resources: integrity, deletes, expansion, related filters."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from invoices.authz import seed
from invoices.bootstrap import authz
from invoices.bootstrap import registry as invoices
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Reference, Resource, ResourceManager, ResourceRegistry
from resource_engine.application import manager as manager_module
from resource_engine.application.relations import dump_read, expansions
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import (
    ConfigurationError,
    InvalidQuery,
    ReferenceInUse,
    ReferenceNotFound,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore

ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"platform-admin"}))
ACCOUNTANT = Principal(subject="ada", issuer="https://sso", roles=frozenset({"accountant"}))
CLERK = Principal(subject="cl", issuer="https://sso", roles=frozenset({"clerk"}))


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
async def manager(monkeypatch) -> ResourceManager:
    store = InMemoryStore.from_registry(invoices)
    manager = ResourceManager(
        invoices,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        audit=True,
    )
    await seed(manager, ADMIN)
    # clerks work on invoices but may not see customers
    monkeypatch.setitem(manager.authorizer.role_source.mapping, "clerk", "clerk")
    await manager.create("authz_roles", {"name": "clerk"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Clerks handle invoices",
            "effect": "allow",
            "subjects": {"roles": ["clerk"]},
            "actions": ["invoices:list", "invoices:get", "invoices:create"],
        },
        actor=ADMIN,
    )
    return manager


async def _customer(manager, name: str, country: str = "ES") -> int:
    tax_id = f"B{abs(hash(name)) % 10**8:08d}"
    data = {"name": name, "tax_id": tax_id, "country": country}
    return (await manager.create("customers", data, actor=ADMIN)).id


async def _invoice(manager, customer_id: int | None, actor=ADMIN) -> int:
    data = {"customer": "x", "amount": 10, "customer_id": customer_id}
    return (await manager.create("invoices", data, actor=actor)).id


# ---------------------------------------------------------------- integrity


async def test_writes_need_an_existing_record(manager):
    acme = await _customer(manager, "ACME")
    invoice = await _invoice(manager, acme)
    assert invoice
    with pytest.raises(ReferenceNotFound) as caught:
        await _invoice(manager, 999)
    assert caught.value.code == "RE-E108"
    assert caught.value.details == {"field": "customer_id", "resource": "customers", "id": 999}
    with pytest.raises(ReferenceNotFound):
        await manager.update("invoices", invoice, {"customer_id": 998}, actor=ADMIN)
    await manager.update("invoices", invoice, {"customer_id": None}, actor=ADMIN)
    assert await _invoice(manager, None)


async def test_the_caller_must_see_the_referenced_record(manager):
    acme = await _customer(manager, "ACME")
    with pytest.raises(ReferenceNotFound):  # clerks cannot read customers: no leak
        await _invoice(manager, acme, actor=CLERK)
    assert await _invoice(manager, None, actor=CLERK)


async def test_deleting_a_referenced_record_is_refused(manager):
    acme = await _customer(manager, "ACME")
    invoice = await _invoice(manager, acme)
    with pytest.raises(ReferenceInUse) as caught:
        await manager.delete("customers", acme, actor=ADMIN)
    assert caught.value.code == "RE-E109"
    assert caught.value.details == {"resource": "invoices", "field": "customer_id", "id": acme}
    await manager.delete("invoices", invoice, actor=ADMIN)
    await manager.delete("customers", acme, actor=ADMIN)


# ---------------------------------------------------------------- expansion


async def test_expand_get_and_list(manager):
    acme = await _customer(manager, "ACME")
    globex = await _customer(manager, "Globex", "FR")
    ids = [await _invoice(manager, c) for c in (acme, globex, acme, None)]

    one = await manager.get("invoices", ids[0], actor=ADMIN, expand=["customer_record"])
    assert expansions(one)["customer_record"].name == "ACME"
    assert dump_read(one)["customer_record"]["tax_id"].startswith("B")

    page = await manager.list(
        "invoices", ListQuery(order_by=["id"]), actor=ADMIN, expand=("customer_record",)
    )
    names = [r and r.name for r in (expansions(i)["customer_record"] for i in page.items)]
    assert names == ["ACME", "Globex", "ACME", None]
    plain = await manager.get("invoices", ids[0], actor=ADMIN)
    assert expansions(plain) == {}
    with pytest.raises(InvalidQuery):
        await manager.get("invoices", ids[0], actor=ADMIN, expand=["vendor"])


async def test_unreadable_related_records_are_null(manager):
    acme = await _customer(manager, "ACME")
    invoice = await _invoice(manager, acme)
    seen = await manager.get("invoices", invoice, actor=CLERK, expand=["customer_record"])
    assert seen.customer_id == acme
    assert expansions(seen) == {"customer_record": None}
    # readers with access get it, with their own field permissions
    ok = await manager.get("invoices", invoice, actor=ACCOUNTANT, expand=["customer_record"])
    assert expansions(ok)["customer_record"].name == "ACME"


# ---------------------------------------------------------- related filters


async def test_filter_by_related_fields(manager, monkeypatch):
    acme = await _customer(manager, "ACME")
    globex = await _customer(manager, "Globex", "FR")
    french = await _invoice(manager, globex)
    await _invoice(manager, acme)
    await _invoice(manager, None)

    page = await manager.list(
        "invoices", ListQuery(filters={"customer_record.country": "FR"}), actor=ADMIN
    )
    assert [i.id for i in page.items] == [french]
    none = await manager.list(
        "invoices", ListQuery(filters={"customer_record.country": "DE"}), actor=ADMIN
    )
    assert none.total == 0
    # callers who cannot see customers match nothing through them
    hidden = await manager.list(
        "invoices", ListQuery(filters={"customer_record.country": "FR"}), actor=CLERK
    )
    assert hidden.total == 0
    with pytest.raises(InvalidQuery, match="customer_record.email"):
        await manager.list(
            "invoices", ListQuery(filters={"customer_record.email": "x"}), actor=ADMIN
        )
    monkeypatch.setattr(manager_module, "MAX_RELATED_MATCHES", 1)
    await _customer(manager, "Initech", "FR")
    with pytest.raises(InvalidQuery, match="more than 1"):
        await manager.list(
            "invoices", ListQuery(filters={"customer_record.country": "FR"}), actor=ADMIN
        )


# -------------------------------------------------------- set_null / cascade


@dataclass
class Order:
    status: str = "open"
    id: int | None = None


@dataclass
class Line:
    order_id: int | None
    sku: str = "x"
    id: int | None = None


@dataclass
class Note:
    order_id: int | None
    text: str = "x"
    id: int | None = None


class OrderIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    status: str = "open"


class LineIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    order_id: int | None = None
    sku: str = "x"


class NoteIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    order_id: int | None = None
    text: str = "x"


def _orders(line_hooks=None, **line_options):
    class OrderResource(Resource):
        model = Order
        schemas = {"create": OrderIO, "read": OrderIO}
        operations = ["get", "create", "delete"]

    class LineResource(Resource):
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["list", "get", "create", "delete"]
        references = {"order_id": Reference("orders", on_delete="cascade", **line_options)}
        hooks = line_hooks or {}

    class NoteResource(Resource):
        model = Note
        schemas = {"create": NoteIO, "update": NoteIO, "read": NoteIO}
        operations = ["get", "create", "update"]
        references = {"order_id": Reference("orders", on_delete="set_null")}

    registry = ResourceRegistry()
    for r in (OrderResource, LineResource, NoteResource):
        registry.register(r)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work), store


async def test_cascade_and_set_null():
    manager, _ = _orders()
    order = (await manager.create("orders", {})).id
    lines = [(await manager.create("lines", {"order_id": order})).id for _ in range(3)]
    note = (await manager.create("notes", {"order_id": order})).id
    await manager.delete("orders", order)
    assert (await manager.list("lines")).total == 0
    assert (await manager.get("notes", note)).order_id is None
    assert lines


async def test_a_failing_dependent_rolls_everything_back():
    def refuse(ctx):
        raise ValueError("lines are locked")

    manager, _ = _orders(line_hooks={"before_delete": [refuse]})
    order = (await manager.create("orders", {})).id
    await manager.create("lines", {"order_id": order})
    note = (await manager.create("notes", {"order_id": order})).id
    with pytest.raises(ValueError, match="locked"):
        await manager.delete("orders", order)
    assert (await manager.get("orders", order)).id == order
    assert (await manager.list("lines")).total == 1
    assert (await manager.get("notes", note)).order_id == order


# ------------------------------------------------------------- declarations


def _registry(*resources):
    registry = ResourceRegistry()
    for r in resources:
        registry.register(r)
    return registry


def _line(**reference):
    class LineResource(Resource):
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["get", "create"]
        references = {"order_id": Reference(**{"resource": "orders", **reference})}

    return LineResource


def test_declarations_are_checked():
    store = InMemoryStore

    def build(*resources):
        registry = _registry(*resources)
        return ResourceManager(registry, store.from_registry(registry).unit_of_work)

    with pytest.raises(ConfigurationError, match="not registered"):
        build(_line())

    class OrderResource(Resource):
        model = Order
        schemas = {"create": OrderIO, "read": OrderIO}
        operations = ["get", "create", "delete"]

    with pytest.raises(ConfigurationError, match="set_null"):
        build(OrderResource, _line(on_delete="set_null"))
    with pytest.raises(ConfigurationError, match="cascade"):
        build(OrderResource, _line(on_delete="cascade"))
    with pytest.raises(ConfigurationError, match="clashes"):
        _line(name="sku").definition()
    with pytest.raises(ConfigurationError, match="on_delete"):
        _line(on_delete="ignore").definition()

    class Bad(Resource):
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["get"]
        references = {"order_id": "orders"}

    with pytest.raises(ConfigurationError, match="must be a Reference"):
        Bad.definition()


def test_cascade_cycles_are_refused():
    class A(Resource):
        name = "a"
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["get", "create", "delete"]
        references = {"order_id": Reference("b", on_delete="cascade")}

    class B(Resource):
        name = "b"
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["get", "create", "delete"]
        references = {"order_id": Reference("a", on_delete="cascade")}

    registry = _registry(A, B)
    with pytest.raises(ConfigurationError, match="cycle"):
        ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)


# --------------------------------------------------------- reverse relations


async def test_list_related_and_reverse_expand(manager):
    acme = await _customer(manager, "ACME")
    globex = await _customer(manager, "Globex", "FR")
    for _ in range(3):
        await _invoice(manager, acme)
    await _invoice(manager, globex)

    page = await manager.list_related(
        "customers", acme, "invoices", ListQuery(limit=2, order_by=["id"]), actor=ADMIN
    )
    assert page.total == 3 and len(page.items) == 2
    assert {i.customer_id for i in page.items} == {acme}
    drafts = await manager.list_related(
        "customers", acme, "invoices", ListQuery(filters={"status": "draft"}), actor=ADMIN
    )
    assert drafts.total == 3

    one = await manager.get("customers", acme, actor=ADMIN, expand=["invoices:2"])
    embedded = expansions(one)["invoices"]
    assert (embedded.total, len(embedded.items), embedded.has_more) == (3, 2, True)
    assert dump_read(one)["invoices"]["has_more"] is True
    default = await manager.get("customers", globex, actor=ADMIN, expand=["invoices"])
    assert expansions(default)["invoices"].limit == manager_module.EXPAND_PAGE_SIZE

    listed = await manager.list("customers", actor=ADMIN, expand=("invoices:1",))
    totals = {c.name: expansions(c)["invoices"].total for c in listed.items}
    assert totals == {"ACME": 3, "Globex": 1}


async def test_reverse_relations_follow_permissions(manager):
    acme = await _customer(manager, "ACME")
    await _invoice(manager, acme)
    # clerks may list invoices but not read customers: the parent is hidden
    with pytest.raises(Exception) as caught:
        await manager.list_related("customers", acme, "invoices", actor=CLERK)
    assert getattr(caught.value, "code", None) in ("RE-E702", "RE-E102")

    # approvers read customers but only see pending/approved invoices
    approver = Principal(subject="pau", issuer="https://sso", roles=frozenset({"approver"}))
    page = await manager.list_related("customers", acme, "invoices", actor=approver)
    assert page.total == 0
    seen = await manager.get("customers", acme, actor=approver, expand=["invoices"])
    assert expansions(seen)["invoices"].total == 0


async def test_bad_reverse_expansions(manager):
    acme = await _customer(manager, "ACME")
    with pytest.raises(InvalidQuery, match="between 1 and"):
        await manager.get("customers", acme, actor=ADMIN, expand=["invoices:0"])
    with pytest.raises(InvalidQuery, match="only reverse relations"):
        await manager.get("invoices", 1, actor=ADMIN, expand=["customer_record:5"])
    with pytest.raises(InvalidQuery, match="no reverse or many-to-many relation"):
        await manager.list_related("customers", acme, "orders", actor=ADMIN)


def test_reverse_names_are_checked():
    class OrderResource(Resource):
        model = Order
        schemas = {"create": OrderIO, "read": OrderIO}
        operations = ["get", "create", "delete"]

    def build(**reference):
        class LineResource(Resource):
            model = Line
            schemas = {"create": LineIO, "read": LineIO}
            operations = ["list", "get", "create"]
            references = {"order_id": Reference("orders", **reference)}

        registry = _registry(OrderResource, LineResource)
        return ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)

    assert set(build().reverse["orders"]) == {"lines"}
    assert set(build(related_name="items").reverse["orders"]) == {"items"}
    assert build(reverse=False).reverse == {}
    for name, clash in (("status", "a field"), ("history", "a route")):
        with pytest.raises(ConfigurationError, match=clash):
            build(related_name=name)


# ---------------------------------------------- nested, batched, many-to-many


async def _tag(manager, name: str) -> int:
    return (await manager.create("tags", {"name": name}, actor=ADMIN)).id


async def test_nested_expansion(manager):
    acme = await _customer(manager, "ACME")
    first = await _invoice(manager, acme)
    await _invoice(manager, acme)

    invoice = await manager.get(
        "invoices", first, actor=ADMIN, expand=["customer_record.invoices:1"]
    )
    customer = expansions(invoice)["customer_record"]
    siblings = expansions(customer)["invoices"]
    assert (siblings.total, len(siblings.items)) == (2, 1)
    dumped = dump_read(invoice)
    assert dumped["customer_record"]["invoices"]["total"] == 2

    customer = await manager.get(
        "customers", acme, actor=ADMIN, expand=["invoices.customer_record"]
    )
    for item in expansions(customer)["invoices"].items:
        assert expansions(item)["customer_record"].name == "ACME"

    with pytest.raises(InvalidQuery, match="no relation 'vendor'"):
        await manager.get("invoices", first, actor=ADMIN, expand=["customer_record.vendor"])
    with pytest.raises(InvalidQuery, match="more than 3 levels"):
        await manager.get(
            "invoices",
            first,
            actor=ADMIN,
            expand=["customer_record.invoices.customer_record.invoices"],
        )


async def test_reverse_expansion_on_lists_is_batched(manager, monkeypatch):
    acme = await _customer(manager, "ACME")
    globex = await _customer(manager, "Globex", "FR")
    for customer, count in ((acme, 3), (globex, 1)):
        for _ in range(count):
            await _invoice(manager, customer)
    calls: list[str] = []
    original = manager.list

    async def counting(resource, *args, **kw):
        calls.append(resource)
        return await original(resource, *args, **kw)

    monkeypatch.setattr(manager, "list", counting)
    page = await manager.list("customers", actor=ADMIN, expand=("invoices:2",))
    assert calls.count("invoices") == 1  # one query for every customer's page
    pages = {c.name: expansions(c)["invoices"] for c in page.items}
    assert (pages["ACME"].total, len(pages["ACME"].items)) == (3, 2)
    assert (pages["Globex"].total, pages["Globex"].has_more) == (1, False)

    calls.clear()
    monkeypatch.setattr(manager_module, "MAX_RELATED_MATCHES", 2)  # too many: one per parent
    again = await manager.list("customers", actor=ADMIN, expand=("invoices:2",))
    assert calls.count("invoices") == 3  # the batch, then one per customer
    assert {c.name: expansions(c)["invoices"].total for c in again.items} == {
        "ACME": 3,
        "Globex": 1,
    }


async def test_many_to_many(manager):
    acme = await _customer(manager, "ACME")
    invoice = await _invoice(manager, acme)
    other = await _invoice(manager, acme)
    urgent, review, unused = [await _tag(manager, n) for n in ("urgent", "review", "unused")]
    for tag in (urgent, review):
        await manager.create("invoice_tags", {"invoice_id": invoice, "tag_id": tag}, actor=ADMIN)
    await manager.create("invoice_tags", {"invoice_id": other, "tag_id": urgent}, actor=ADMIN)

    tags = await manager.list_related(
        "invoices", invoice, "tags", ListQuery(order_by=["name"]), actor=ADMIN
    )
    assert [t.name for t in tags.items] == ["review", "urgent"]
    tagged = await manager.list_related("tags", urgent, "invoices", actor=ADMIN)
    assert {i.id for i in tagged.items} == {invoice, other}
    assert (await manager.list_related("tags", unused, "invoices", actor=ADMIN)).total == 0

    one = await manager.get("invoices", invoice, actor=ADMIN, expand=["tags"])
    assert {t.name for t in expansions(one)["tags"].items} == {"review", "urgent"}
    listed = await manager.list("invoices", actor=ADMIN, expand=("tags:1",))
    assert {i.id: expansions(i)["tags"].total for i in listed.items} == {invoice: 2, other: 1}

    with pytest.raises(Exception, match="already has this tag"):
        await manager.create("invoice_tags", {"invoice_id": invoice, "tag_id": urgent}, actor=ADMIN)
    # deleting a tag cascades to its assignments
    await manager.delete("tags", urgent, actor=ADMIN)
    assert (await manager.list_related("invoices", other, "tags", actor=ADMIN)).total == 0


async def test_links_follow_permissions(manager):
    acme = await _customer(manager, "ACME")
    invoice = await _invoice(manager, acme)
    tag = await _tag(manager, "urgent")
    await manager.create("invoice_tags", {"invoice_id": invoice, "tag_id": tag}, actor=ADMIN)
    # clerks read invoices, but not tags or their assignments
    seen = await manager.get("invoices", invoice, actor=CLERK, expand=["tags"])
    assert expansions(seen) == {"tags": None}


def test_link_declarations_are_checked():
    class TagLink(Resource):
        name = "links"
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["list", "get"]
        references = {"order_id": Reference("orders")}
        link = ("order_id", "sku")

    with pytest.raises(ConfigurationError, match="two different reference fields"):
        TagLink.definition()

    class Names(Resource):
        name = "names"
        model = Line
        schemas = {"create": LineIO, "read": LineIO}
        operations = ["list", "get"]
        link_names = {"order_id": "x"}

    with pytest.raises(ConfigurationError, match="link_names needs link"):
        Names.definition()
