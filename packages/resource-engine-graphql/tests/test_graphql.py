"""The GraphQL adapter on the invoices example: schema, queries, mutations,
relations, permissions, errors and HTTP."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from invoices.authz import seed
from invoices.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_graphql import GraphQLAdapter, mount_graphql

ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"platform-admin"}))
ACCOUNTANT = Principal(subject="ada", issuer="https://sso", roles=frozenset({"accountant"}))
APPROVER = Principal(subject="pau", issuer="https://sso", roles=frozenset({"approver"}))


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
async def manager() -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        audit=True,
    )
    await seed(manager, ADMIN)
    return manager


@pytest.fixture
def adapter(manager) -> GraphQLAdapter:
    return GraphQLAdapter(registry, manager)


async def run(adapter, query, actor=ADMIN, **variables):
    return await adapter.execute(query, variables or None, actor=actor)


async def _setup(adapter):
    acme = await run(
        adapter,
        'mutation { createCustomer(data: {name: "ACME", taxId: "B111"}) { id name country } }',
    )
    customer = acme["data"]["createCustomer"]["id"]
    ids = []
    for amount in ("10.50", "200"):
        created = await run(
            adapter,
            """mutation($data: InvoiceCreateInput!) {
                 createInvoice(data: $data, idempotencyKey: null) { id status amount }
               }""",
            data={"customer": "ACME", "amount": amount, "customerId": customer},
        )
        assert "errors" not in created, created
        ids.append(created["data"]["createInvoice"]["id"])
    return customer, ids


def test_schema(adapter):
    sdl = adapter.sdl()
    for expected in (
        "type Invoice {",
        "customerRecord: Customer",
        "tags(",
        "allowedTransitions: [String!]",
        "): InvoicePage!",
        "createInvoice(data: InvoiceCreateInput!",
        "approveInvoice(id: Int!",
        "rejectInvoice(id: Int!, data: RejectInvoiceInput!",
        "recalculateTotalsInvoice(id: Int!, data: ApplyDiscountInput!",
        "scalar Decimal",
        "input InvoiceFilter",
        "customerRecord: CustomerFieldFilter",
    ):
        assert expected in sdl, expected


async def test_queries_and_relations(adapter):
    customer, ids = await _setup(adapter)
    tag = await run(adapter, 'mutation { createTag(data: {name: "urgent"}) { id } }')
    tag_id = tag["data"]["createTag"]["id"]
    await run(
        adapter,
        f"mutation {{ createInvoiceTag(data: {{invoiceId: {ids[0]}, tagId: {tag_id}}}) {{ id }} }}",
    )
    result = await run(
        adapter,
        """query($country: String) {
             invoices(orderBy: ["-amount"], filter: {customerRecord: {country: $country}}) {
               total
               items {
                 id amount issueDate allowedTransitions
                 customerRecord { name invoices(limit: 1) { total items { id } } }
                 tags { total items { name } }
               }
             }
           }""",
        country="ES",
    )
    assert "errors" not in result, result
    page = result["data"]["invoices"]
    assert page["total"] == 2
    first, second = page["items"]
    assert (first["amount"], second["amount"]) == ("200", "10.50")
    assert first["allowedTransitions"] == ["submit"]
    assert first["customerRecord"]["name"] == "ACME"
    assert first["customerRecord"]["invoices"]["total"] == 2
    assert second["tags"] == {"total": 1, "items": [{"name": "urgent"}]}

    one = await run(adapter, f"{{ customer(id: {customer}) {{ name invoices {{ total }} }} }}")
    assert one["data"]["customer"] == {"name": "ACME", "invoices": {"total": 2}}


async def test_references_are_batched(adapter, manager, monkeypatch):
    await _setup(adapter)
    calls: list[list] = []
    original = manager.get_many

    async def counting(resource, ids, **kw):
        calls.append(list(ids))
        return await original(resource, ids, **kw)

    monkeypatch.setattr(manager, "get_many", counting)
    result = await run(adapter, "{ invoices { items { customerRecord { name } } } }")
    assert [i["customerRecord"]["name"] for i in result["data"]["invoices"]["items"]] == [
        "ACME",
        "ACME",
    ]
    assert len(calls) == 1  # one batched read for both invoices


async def test_mutations(adapter):
    _customer, ids = await _setup(adapter)
    invoice = ids[0]
    updated = await run(
        adapter,
        f'mutation {{ updateInvoice(id: {invoice}, data: {{notes: "call"}}, expectedVersion: 1)'
        " { notes version } }",
    )
    assert updated["data"]["updateInvoice"] == {"notes": "call", "version": 2}
    stale = await run(
        adapter,
        f'mutation {{ updateInvoice(id: {invoice}, data: {{notes: "x"}}, expectedVersion: 1)'
        " { version } }",
    )
    assert stale["errors"][0]["extensions"]["code"] == "RE-E105"
    submitted = await run(adapter, f"mutation {{ submitInvoice(id: {invoice}) {{ status }} }}")
    assert submitted["data"]["submitInvoice"]["status"] == "pending"
    rejected = await run(
        adapter,
        f'mutation {{ rejectInvoice(id: {invoice}, data: {{reason: "Falta NIF"}}) '
        "{ status rejectionReason history { toState } } }",
    )
    data = rejected["data"]["rejectInvoice"]
    assert data["status"] == "rejected"
    assert [h["toState"] for h in data["history"]] == ["draft", "pending", "rejected"]
    deleted = await run(adapter, f"mutation {{ deleteInvoice(id: {ids[1]}) }}")
    assert deleted["data"] == {"deleteInvoice": True}


async def test_errors(adapter):
    missing = await run(adapter, "{ invoice(id: 999) { id } }")
    assert missing["data"] == {"invoice": None}
    error = missing["errors"][0]
    assert error["extensions"]["code"] == "RE-E102"
    assert error["path"] == ["invoice"]
    invalid = await run(
        adapter, 'mutation { createInvoice(data: {customer: "", amount: "1"}) { id } }'
    )
    assert invalid["errors"][0]["extensions"]["code"] == "RE-E101"
    domain = await run(
        adapter, 'mutation { createInvoice(data: {customer: "x", amount: "-1"}) { id } }'
    )
    assert domain["errors"][0]["extensions"]["code"] == "INVOICE"
    unknown = await run(adapter, "{ invoices { items { nope } } }")
    assert "nope" in unknown["errors"][0]["message"] and unknown.get("data") is None


async def test_permissions(adapter):
    _customer, ids = await _setup(adapter)
    await run(
        adapter, f'mutation {{ updateInvoice(id: {ids[0]}, data: {{notes: "secret"}}) {{ id }} }}'
    )
    await run(adapter, f"mutation {{ submitInvoice(id: {ids[0]}) {{ id }} }}")
    # accountants read invoices without their internal notes
    seen = await run(adapter, f"{{ invoice(id: {ids[0]}) {{ notes amount }} }}", actor=ACCOUNTANT)
    assert seen["data"]["invoice"] == {"notes": None, "amount": "10.50"}
    denied = await run(
        adapter, 'mutation { createInvoice(data: {customer: "x", amount: "1"}) { id } }',
        actor=ACCOUNTANT,
    )  # fmt: skip
    assert denied["errors"][0]["extensions"]["code"] == "RE-E702"
    # approvers only see pending/approved invoices, and customers through them
    visible = await run(adapter, "{ invoices { total items { id customerRecord { name } } } }",
                        actor=APPROVER)  # fmt: skip
    assert visible["data"]["invoices"]["total"] == 1


async def test_depth_limit(manager):
    shallow = GraphQLAdapter(registry, manager, max_depth=4)
    deep = await run(shallow, "{ invoices { items { customerRecord { invoices { total } } } } }")
    assert deep["errors"][0]["extensions"]["code"] == "QUERY_TOO_DEEP"
    fragment = await run(
        shallow,
        "query { invoices { items { ...F } } } fragment F on Invoice { customerRecord { name } }",
    )
    assert "errors" not in fragment
    introspection = await run(shallow, "{ __schema { types { name fields { name } } } }")
    assert "errors" not in introspection


def test_http(manager):
    app = FastAPI()
    mount_graphql(app, registry, manager, actor_dependency=lambda: ADMIN)
    with TestClient(app) as client:
        created = client.post(
            "/graphql",
            json={
                "query": "mutation($d: CustomerCreateInput!) { createCustomer(data: $d) { id } }",
                "variables": {"d": {"name": "ACME", "taxId": "B999"}},
            },
        )
        assert created.status_code == 200 and "errors" not in created.json()
        listed = client.get("/graphql", params={"query": "{ customers { total } }"})
        assert listed.json() == {"data": {"customers": {"total": 1}}}
        assert (
            client.get(
                "/graphql", params={"query": "mutation { deleteCustomer(id: 1) }"}
            ).status_code
            == 405
        )
        assert client.post("/graphql", content=b"nope").status_code == 400
        assert client.post("/graphql", json={"variables": {}}).status_code == 400
        assert "type Customer" in client.get("/graphql/schema.graphql").text
