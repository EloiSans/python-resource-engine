"""The standalone explorer (mount_explorer): its page, config and assets, and
the live event stream over server-sent events."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Header
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import (
    AuthzConfig,
    IdpRoleSource,
    PolicyAuthorizer,
    SystemRole,
    system_principal,
)
from resource_engine.explorer import register_explorer
from resource_engine.messaging import EventEnvelope, EventHub
from resource_engine.messaging.delivery import TransportEventPublisher
from resource_engine.ports.activity import DeliveryRecord
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryActivityLog, InMemoryStore
from resource_engine_fastapi import mount_explorer, mount_resources


@dataclass
class Note:
    text: str
    secret: str = "s"
    id: int | None = None


class NoteIn(BaseModel):
    text: str
    secret: str = "s"


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "get", "create"]
    restricted_fields = ["secret"]


PEOPLE = {
    "reader": Principal(subject="r", issuer="i", roles=frozenset({"readers"})),
    "watcher": Principal(subject="w", issuer="i", roles=frozenset({"watchers"})),
    "nobody": Principal(subject="n", issuer="i"),
}


def actor(authorization: str | None = Header(None)) -> Principal | None:
    return PEOPLE.get((authorization or "").removeprefix("Bearer "))


def build(*, ui: bool = True):  # noqa: ANN201
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_authz()
    registry.register_audit()
    register_explorer(registry)
    store = InMemoryStore.from_registry(registry)
    hub = EventHub()
    config = AuthzConfig(
        role_source=IdpRoleSource({"readers": "reader", "watchers": "watcher"}),
        system_roles=[
            SystemRole("reader", grants=["notes:get", "notes:list", "notes:create"]),
            SystemRole("watcher", grants=["notes:get", "events:read"]),
        ],
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(config, store.unit_of_work),
        event_publisher=TransportEventPublisher(hub),
        audit=True,
    )
    activity = InMemoryActivityLog()
    app = FastAPI(title="Notes")
    mount_resources(app, registry, manager, actor_dependency=actor)
    mount_explorer(app, manager, actor_dependency=actor, events=hub, activity=activity, ui=ui)
    return app, manager, hub, activity


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# ------------------------------------------------------------ the page


async def test_the_page_config_and_assets_need_no_build_step():
    app, *_ = build()
    async with client(app) as api:
        page = await api.get("/explorer/", headers={"Accept-Language": "ca-ES,en;q=0.5"})
        assert page.status_code == 200 and '<html lang="ca">' in page.text
        assert "Explorador" in page.text and "content-security-policy" in page.headers
        assert (await api.get("/explorer")).status_code == 307
        config = await api.get("/explorer/config.js", params={"lang": "es"})
        data = json.loads(config.text.removeprefix("window.CONSOLE = ").rstrip(";\n"))
        assert data["standalone"] is True and data["labels"]["connect"] == "Conectar"
        assert {o["path"] for o in data["resources"]["notes"]} >= {"/notes", "/notes/{entity_id}"}
        assert data["events"]["stream"] == "/_explorer/events/stream"
        assert data["events"]["deliveries"] == "/_explorer/events/{message_id}/deliveries"
        assert {"type": "ResourceCreated", "versions": ["1"]} in data["events"]["types"]
        for name in ("console.js", "events.js", "explorer.js", "explorer.css"):
            assert (await api.get(f"/explorer/assets/{name}")).status_code == 200
        assert (await api.get("/explorer/assets/../../etc")).status_code == 404
        paths = (await api.get("/openapi.json")).json()["paths"]
        assert "/_explorer/events/stream" in paths and "/explorer/" not in paths


async def test_ui_false_serves_only_the_endpoints():
    app, *_ = build(ui=False)
    async with client(app) as api:
        assert (await api.get("/explorer/")).status_code == 404
        refused = await api.get("/_explorer/events/stream")  # no token: refused before streaming
        assert refused.status_code == 403


# ---------------------------------------------------------- the stream


@contextlib.asynccontextmanager
async def serve(app: FastAPI):  # noqa: ANN201
    """The app on a real server in this event loop (httpx's ASGI transport
    buffers whole responses, so it can't read a stream)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def read_events(response: httpx.Response, count: int) -> list[dict]:
    events, data = [], None
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))
        elif line == "" and data is not None:
            events.append(data)
            data = None
            if len(events) == count:
                return events
    return events


async def wait_for_subscriber(hub: EventHub) -> None:
    for _ in range(200):
        if hub.subscribers:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the stream never subscribed")


async def test_the_stream_sends_what_the_caller_may_see():
    app, manager, hub, activity = build()
    async with serve(app) as url, httpx.AsyncClient(base_url=url, timeout=5) as api:
        async with api.stream(
            "GET", "/_explorer/events/stream", headers={"Authorization": "Bearer reader"}
        ) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            await wait_for_subscriber(hub)
            reader = PEOPLE["reader"]
            note = await manager.create("notes", {"text": "hola"}, actor=reader)
            await hub.publish([EventEnvelope(type="Ping", payload={},
                                             occurred_at=datetime.now(UTC))])  # fmt: skip
            await manager.create("notes", {"text": "adeu"}, actor=reader)
            [created, second] = await asyncio.wait_for(read_events(response, 2), 5)
        assert created["envelope"]["type"] == "ResourceCreated"
        assert created["entity_id"] == note.id and created["record"]["text"] == "hola"
        assert "secret" not in created["record"]  # read as the reader may see it
        assert "payload" not in created["envelope"] and not created["payload_visible"]
        assert second["record"]["text"] == "adeu"  # the event about no record was skipped

        await activity.record_delivery(
            DeliveryRecord("mailer", created["envelope"]["id"], "ResourceCreated", "ack", 0.02)
        )
        found = await api.get(
            f"/_explorer/events/{created['envelope']['id']}/deliveries",
            headers={"Authorization": "Bearer watcher"},
        )
        assert [(d["consumer"], d["outcome"]) for d in found.json()] == [("mailer", "ack")]
    audit = await manager.list("audit_log", actor=system_principal("tests"))
    assert any(e.action == "events:stream" and e.outcome == "ok" for e in audit.items)


async def test_events_read_sees_payloads_and_events_about_no_record():
    app, manager, hub, _ = build()
    async with (
        serve(app) as url,
        httpx.AsyncClient(base_url=url, timeout=5) as api,
        api.stream(
            "GET",
            "/_explorer/events/stream",
            params={"type": "Ping"},
            headers={"Authorization": "Bearer watcher"},
        ) as response,
    ):
        await wait_for_subscriber(hub)
        await hub.publish([EventEnvelope(type="Ping", payload={"n": 1},
                                         occurred_at=datetime.now(UTC))])  # fmt: skip
        [ping] = await asyncio.wait_for(read_events(response, 1), 5)
    assert ping["payload_visible"] and ping["envelope"]["payload"] == {"n": 1}


@pytest.mark.parametrize("who", ["nobody", None])
async def test_callers_who_can_read_nothing_are_refused(who):
    app, manager, *_ = build()
    headers = {"Authorization": f"Bearer {who}"} if who else {}
    async with client(app) as api:
        refused = await api.get("/_explorer/events/stream", headers=headers)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "RE-E702"
        unknown = await api.get("/_explorer/events/stream?resource=nope", headers=headers)
        assert unknown.status_code == 404
        no_deliveries = await api.get("/_explorer/events/x/deliveries", headers=headers)
        assert no_deliveries.status_code == 403


@pytest.mark.parametrize("locale", ["ca", "es"])
def test_every_text_is_translated(locale):
    from resource_engine_fastapi.explorer.labels import LABELS, labels

    translated = labels(locale)
    missing = [key for key, text in LABELS.items() if translated[key] == text and key != "header"]
    assert missing == []
