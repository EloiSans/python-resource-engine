"""Subscriptions (fed by an EventHub), query cost limits and persisted queries."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from invoices.authz import seed
from invoices.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import AuthenticationFailed
from resource_engine.messaging import EventEnvelope, EventHub, FanoutPublisher
from resource_engine.messaging.delivery import TransportEventPublisher
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_graphql import GraphQLAdapter, PersistedQueries, mount_graphql, query_cost

ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"platform-admin"}))
APPROVER = Principal(subject="pau", issuer="https://sso", roles=frozenset({"approver"}))
NOW = datetime.now(UTC)
NOBODY = Principal(subject="eve", issuer="https://sso", roles=frozenset())

CHANGES = """subscription($id: Int, $events: [String!]) {
  invoiceChanged(id: $id, events: $events) { event id record { id status } }
}"""


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def hub() -> EventHub:
    return EventHub(queue_size=10)


@pytest.fixture
async def manager(hub) -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        event_publisher=TransportEventPublisher(hub),
    )
    await seed(manager, ADMIN)
    return manager


@pytest.fixture
def adapter(manager, hub) -> GraphQLAdapter:
    return GraphQLAdapter(registry, manager, hub=hub)


async def _collect(adapter, hub, actor, count, **variables):
    """Start a subscription and return (task collecting ``count`` results)."""
    stream = adapter.subscribe(CHANGES, variables or None, actor=actor)

    async def take():
        out = []
        async for result in stream:
            out.append(result)
            if "errors" in result or len(out) == count:
                break
        await stream.aclose()
        return out

    before = hub.subscribers
    task = asyncio.create_task(take())
    for _ in range(50):
        if hub.subscribers > before or task.done():
            break
        await asyncio.sleep(0)
    return task


async def settle():
    """Let the subscriber read each change before the next one (records are
    re-read when the event is delivered, so they show the state at that time)."""
    await asyncio.sleep(0.05)


async def _invoice(manager, amount="10"):
    return await manager.create("invoices", {"customer": "ACME", "amount": amount}, actor=ADMIN)


# ------------------------------------------------------------------ hub


async def test_hub_fans_out_filters_and_drops_the_oldest():
    hub = EventHub(queue_size=2)
    everything = hub.subscribe()
    only_b = hub.subscribe(lambda e: e.type == "B")
    events = [EventEnvelope(type=t, payload={}, occurred_at=NOW) for t in ("A", "B", "C")]
    await hub.publish(events)
    assert everything.overflowed == 1
    assert [everything.queue.get_nowait().type for _ in range(2)] == ["B", "C"]
    assert only_b.queue.get_nowait().type == "B"
    hub.close()
    assert hub.subscribers == 0
    assert [e async for e in only_b] == []


async def test_fanout_reports_the_ids_one_publisher_rejected():
    from resource_engine.errors import PublishFailed

    class Broken:
        async def publish(self, messages):
            raise RuntimeError("broker down")

    hub = EventHub()
    seen = hub.subscribe()
    envelope = EventEnvelope(type="A", payload={}, occurred_at=NOW)
    with pytest.raises(PublishFailed) as caught:
        await FanoutPublisher(Broken(), hub).publish([envelope])
    assert caught.value.failed_ids == [envelope.id]
    assert seen.queue.get_nowait().id == envelope.id  # the hub still got it


# ---------------------------------------------------------- subscriptions


async def test_changes_are_read_as_the_subscriber(adapter, manager, hub):
    task = await _collect(adapter, hub, APPROVER, 2)
    invoice = await _invoice(manager)  # draft: the approver cannot see it
    await settle()
    await manager.transition("invoices", invoice.id, "submit", actor=ADMIN)  # pending: visible
    await settle()
    await manager.transition("invoices", invoice.id, "reject", {"reason": "duplicate"}, actor=ADMIN)
    await settle()
    # rejected: hidden again
    other = await _invoice(manager)
    await settle()
    await manager.transition("invoices", other.id, "submit", actor=ADMIN)
    await settle()
    results = await asyncio.wait_for(task, 2)
    changes = [r["data"]["invoiceChanged"] for r in results]
    assert [(c["event"], c["id"], c["record"]["status"]) for c in changes] == [
        ("TransitionExecuted", invoice.id, "pending"),
        ("TransitionExecuted", other.id, "pending"),
    ]
    assert hub.subscribers == 0  # closed with the stream


async def test_one_record_and_event_filter(adapter, manager, hub):
    invoice = await _invoice(manager)
    await settle()
    other = await _invoice(manager)
    await settle()
    task = await _collect(adapter, hub, ADMIN, 1, id=invoice.id, events=["TransitionExecuted"])
    await manager.update("invoices", invoice.id, {"customer": "Other"}, actor=ADMIN)
    await settle()
    await manager.transition("invoices", other.id, "submit", actor=ADMIN)
    await settle()
    await manager.transition("invoices", invoice.id, "submit", actor=ADMIN)
    await settle()
    [result] = await asyncio.wait_for(task, 2)
    change = result["data"]["invoiceChanged"]
    assert (change["event"], change["id"]) == ("TransitionExecuted", invoice.id)


async def test_deletes_only_for_records_the_subscriber_saw(adapter, manager, hub):
    seen = await _invoice(manager)
    await settle()
    unseen = await _invoice(manager)
    await settle()
    task = await _collect(adapter, hub, ADMIN, 3, events=["ResourceUpdated", "ResourceDeleted"])
    await manager.update("invoices", seen.id, {"customer": "Seen"}, actor=ADMIN)
    await settle()
    await manager.delete("invoices", unseen.id, actor=ADMIN)
    await settle()
    await manager.delete("invoices", seen.id, actor=ADMIN)
    await settle()
    marker = await _invoice(manager)
    await settle()
    await manager.update("invoices", marker.id, {"customer": "End"}, actor=ADMIN)
    await settle()
    results = await asyncio.wait_for(task, 2)
    changes = [r["data"]["invoiceChanged"] for r in results]
    assert [(c["event"], c["id"], c["record"] is None) for c in changes] == [
        ("ResourceUpdated", seen.id, False),
        ("ResourceDeleted", seen.id, True),
        ("ResourceUpdated", marker.id, False),
    ]


async def test_subscribing_needs_read_permission(adapter, manager, hub):
    results = await asyncio.wait_for(await _collect(adapter, hub, NOBODY, 1), 2)
    assert results[0]["errors"][0]["extensions"]["code"] == "RE-E702"
    hidden = await _invoice(manager)  # a draft
    results = await asyncio.wait_for(await _collect(adapter, hub, APPROVER, 1, id=hidden.id), 2)
    assert results[0]["errors"]
    assert hub.subscribers == 0


async def test_no_subscriptions_without_a_hub_or_over_http(manager, adapter):
    assert "invoiceChanged" not in GraphQLAdapter(registry, manager).sdl()
    assert "invoiceChanged(" in adapter.sdl()
    result = await adapter.execute(CHANGES, actor=ADMIN)
    assert "WebSocket" in result["errors"][0]["message"]


# ---------------------------------------------------------------- cost


def test_cost_multiplies_page_sizes(adapter):
    from graphql import parse

    def cost(query, **variables):
        return query_cost(adapter.schema, parse(query), None, variables)

    assert cost("{ invoice(id: 1) { id status } }") == 3
    assert cost("{ invoices(limit: 10) { items { id } } }") == 1 + 10 * (1 + 1)
    assert cost("query($n: Int) { invoices(limit: $n) { items { id } } }", n=5) == 11
    nested = "{ customers(limit: 10) { items { invoices(limit: 10) { items { id } } } } }"
    assert cost(nested) == 1 + 10 * (1 + 1 + 10 * 2)
    fragment = "{ invoices(limit: 2) { ...F } } fragment F on InvoicePage { items { id } }"
    assert cost(fragment) == 5
    assert cost("{ __schema { types { name } } }") == 0


async def test_costly_queries_are_refused(manager):
    adapter = GraphQLAdapter(registry, manager, max_cost=100)
    result = await adapter.execute(
        "{ customers(limit: 50) { items { invoices(limit: 50) { items { id } } } } }",
        actor=ADMIN,
    )
    assert result["errors"][0]["extensions"]["code"] == "QUERY_TOO_COSTLY"
    ok = await adapter.execute("{ invoices(limit: 10) { items { id } } }", actor=ADMIN)
    assert "errors" not in ok


# ------------------------------------------------------- persisted queries

QUERY = "{ invoices(limit: 5) { total } }"
SHA = hashlib.sha256(QUERY.encode()).hexdigest()


def _apq(digest=SHA):
    return {"persistedQuery": {"version": 1, "sha256Hash": digest}}


async def test_automatic_persisted_queries(manager):
    adapter = GraphQLAdapter(registry, manager, persisted=PersistedQueries(cache_size=1))
    miss = await adapter.execute(None, actor=ADMIN, extensions=_apq())
    assert miss["errors"][0]["message"] == "PersistedQueryNotFound"
    assert miss["errors"][0]["extensions"]["code"] == "PERSISTED_QUERY_NOT_FOUND"
    learned = await adapter.execute(QUERY, actor=ADMIN, extensions=_apq())
    assert learned["data"]["invoices"]["total"] == 0
    hit = await adapter.execute(None, actor=ADMIN, extensions=_apq())
    assert hit == learned
    bad = await adapter.execute(QUERY, actor=ADMIN, extensions=_apq("0" * 64))
    assert bad["errors"][0]["extensions"]["code"] == "PERSISTED_QUERY_HASH_MISMATCH"
    plain = await adapter.execute("{ invoices { total } }", actor=ADMIN)
    assert "errors" not in plain
    other = "{ customers { total } }"
    await adapter.execute(other, actor=ADMIN, extensions=_apq(PersistedQueries.hash(other)))
    evicted = await adapter.execute(None, actor=ADMIN, extensions=_apq())
    assert evicted["errors"][0]["extensions"]["code"] == "PERSISTED_QUERY_NOT_FOUND"


async def test_allow_list(manager):
    adapter = GraphQLAdapter(
        registry, manager, persisted=PersistedQueries({SHA: QUERY}, allow_only=True)
    )
    ok = await adapter.execute(None, actor=ADMIN, extensions=_apq())
    assert ok["data"]["invoices"]["total"] == 0
    for query, extensions in (
        ("{ customers { total } }", None),
        ("{ customers { total } }", _apq(PersistedQueries.hash("{ customers { total } }"))),
    ):
        refused = await adapter.execute(query, actor=ADMIN, extensions=extensions)
        assert refused["errors"][0]["extensions"]["code"] in (
            "PERSISTED_QUERY_REQUIRED",
            "PERSISTED_QUERY_NOT_FOUND",
        )


# ---------------------------------------------------------------- HTTP / WS


class _Tokens:
    async def authenticate(self, token: str) -> Principal:
        if token == "admin":
            return ADMIN
        raise AuthenticationFailed("Invalid token", reason="invalid")


def _app(manager, hub, **kw):
    app = FastAPI()
    mount_graphql(app, registry, manager, hub=hub, authenticator=_Tokens(), **kw)
    return app


def test_http_persisted_queries(manager, hub):
    import json

    app = _app(manager, hub, actor_dependency=lambda: ADMIN, persisted=PersistedQueries())
    with TestClient(app) as client:
        miss = client.post("/graphql", json={"extensions": _apq()}).json()
        assert miss["errors"][0]["extensions"]["code"] == "PERSISTED_QUERY_NOT_FOUND"
        client.post("/graphql", json={"query": QUERY, "extensions": _apq()})
        hit = client.get("/graphql", params={"extensions": json.dumps(_apq())}).json()
        assert hit["data"]["invoices"]["total"] == 0
        mutation = 'mutation { createCustomer(data: {name: "A", taxId: "B1"}) { id } }'
        assert client.get("/graphql", params={"query": mutation}).status_code == 405


def test_websocket_subscription(manager, hub):
    from starlette.websockets import WebSocketDisconnect

    app = _app(manager, hub, actor_dependency=lambda: ADMIN)
    with (
        TestClient(app) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json({"type": "connection_init", "payload": {"authorization": "Bearer admin"}})
        assert ws.receive_json() == {"type": "connection_ack"}
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json(
            {"id": "1", "type": "subscribe", "payload": {"query": CHANGES, "variables": {}}}
        )
        for _ in range(50):  # wait until the subscription is listening
            if hub.subscribers:
                break
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
        created = client.post(
            "/graphql",
            json={"query": 'mutation { createInvoice(data: {customer: "A", amount: "1"}) { id } }'},
        ).json()
        invoice = created["data"]["createInvoice"]["id"]
        message = ws.receive_json()
        assert message["id"] == "1" and message["type"] == "next"
        change = message["payload"]["data"]["invoiceChanged"]
        assert (change["event"], change["id"]) == ("ResourceCreated", invoice)
        ws.send_json(
            {"id": "2", "type": "subscribe", "payload": {"query": "{ invoices { total } }"}}
        )
        assert ws.receive_json()["payload"]["data"]["invoices"]["total"] == 1
        assert ws.receive_json() == {"id": "2", "type": "complete"}
        ws.send_json({"id": "1", "type": "subscribe", "payload": {"query": CHANGES}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4409


@pytest.mark.parametrize(
    ("first", "code"),
    [
        ({"type": "subscribe", "id": "1", "payload": {}}, 4401),
        ({"type": "connection_init", "payload": {}}, 4403),
        ({"type": "connection_init", "payload": {"authorization": "Bearer nope"}}, 4403),
    ],
)
def test_websocket_needs_init_and_a_token(manager, hub, first, code):
    from starlette.websockets import WebSocketDisconnect

    with (
        TestClient(_app(manager, hub)) as client,
        client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"]) as ws,
    ):
        ws.send_json(first)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == code
