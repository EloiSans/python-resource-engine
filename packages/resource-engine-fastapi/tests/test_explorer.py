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
    "operator": Principal(subject="o", issuer="i", roles=frozenset({"operators"})),
}


def actor(authorization: str | None = Header(None)) -> Principal | None:
    return PEOPLE.get((authorization or "").removeprefix("Bearer "))


def build():  # noqa: ANN201
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_authz()
    registry.register_audit()
    register_explorer(registry)
    store = InMemoryStore.from_registry(registry)
    hub = EventHub()
    config = AuthzConfig(
        role_source=IdpRoleSource(
            {"readers": "reader", "watchers": "watcher", "operators": "operator"}
        ),
        system_roles=[
            SystemRole("reader", grants=["notes:get", "notes:list", "notes:create"]),
            SystemRole("watcher", grants=["notes:get", "events:read"]),
            SystemRole("operator", grants=["notes:get", "jobs:read", "jobs:run"]),
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
    mount_explorer(app, manager, actor_dependency=actor, events=hub, activity=activity, jobs=True)
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
        assert 'src="/explorer/config.js?lang=ca"' in page.text  # the texts follow the page
        assert page.headers["cache-control"] == "no-store"
        assert (await api.get("/explorer")).status_code == 307
        config = await api.get("/explorer/config.js", params={"lang": "es"})
        data = json.loads(config.text.removeprefix("window.CONSOLE = ").rstrip(";\n"))
        assert data["labels"]["connect"] == "Conectar"
        assert {o["path"] for o in data["resources"]["notes"]} >= {"/notes", "/notes/{entity_id}"}
        assert data["events"]["stream"] == "/_explorer/events/stream"
        assert data["events"]["deliveries"] == "/_explorer/events/{message_id}/deliveries"
        assert {"type": "ResourceCreated", "versions": ["1"]} in data["events"]["types"]
        for name in ("console.js", "events.js", "explorer.js", "explorer.css"):
            assert (await api.get(f"/explorer/assets/{name}")).status_code == 200
        assert (await api.get("/explorer/assets/../../etc")).status_code == 404
        paths = (await api.get("/openapi.json")).json()["paths"]
        assert not [p for p in paths if "explorer" in p]  # a tool, not the app's API


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
    from importlib.resources import files

    from babel.messages.pofile import read_po
    from resource_engine_fastapi.explorer.labels import LABELS

    po = (
        files("resource_engine_fastapi.explorer")
        / "locale"
        / locale
        / "LC_MESSAGES"
        / "explorer.po"
    )
    with po.open("rb") as handle:
        catalog = {m.id: m.string for m in read_po(handle) if m.id}
    assert [text for text in LABELS.values() if not catalog.get(text)] == []


# ---------------------------------------------------------------- jobs


async def test_jobs_run_now_through_the_worker():
    from resource_engine.jobs import JobRunner
    from resource_engine.testing import InMemoryLocks

    app, manager, _, activity = build()
    runs = []

    async def report() -> None:
        runs.append(1)

    runner = JobRunner(locks=InMemoryLocks(), owner="worker-1", activity=activity)
    runner.add("report", report, every=3600, single=True)
    await runner.register()
    operator = {"Authorization": "Bearer operator"}
    async with client(app) as api:
        listed = (await api.get("/_explorer/jobs", headers=operator)).json()
        [job] = listed["jobs"]
        assert job["name"] == "report" and job["single"] and job["workers"] == ["worker-1"]
        assert job["last_run"] is None and listed["workers"][0]["jobs"] == ["report"]

        started = await api.post("/_explorer/jobs/report/run", headers=operator)
        assert started.status_code == 202 and started.json()["status"] == "pending"
        request_id = started.json()["id"]
        assert (await runner.serve_requests()) == 1 and runs == [1]
        status = await api.get(f"/_explorer/jobs/requests/{request_id}", headers=operator)
        assert status.json()["status"] == "ok" and status.json()["claimed_by"] == "worker-1"
        history = await api.get("/_explorer/jobs/report/runs", headers=operator)
        assert [r["outcome"] for r in history.json()] == ["ok"]
        assert (await api.get("/_explorer/jobs", headers=operator)).json()["jobs"][0]["last_run"][
            "outcome"
        ] == "ok"

        unknown = await api.post("/_explorer/jobs/nope/run", headers=operator)
        assert unknown.status_code == 404
        watcher = {"Authorization": "Bearer watcher"}
        assert (await api.get("/_explorer/jobs", headers=watcher)).status_code == 403
        assert (await api.post("/_explorer/jobs/report/run", headers=watcher)).status_code == 403
        note = await manager.create("notes", {"text": "x"}, actor=PEOPLE["reader"])
        timers = await api.get(f"/_explorer/timers/notes/{note.id}", headers=operator)
        assert timers.status_code == 200 and timers.json() == []  # notes have no timers
        config = await api.get("/explorer/config.js")
        assert '"jobs": {"list": "/_explorer/jobs"' in config.text
    audit = await manager.list("audit_log", actor=system_principal("tests"))
    actions = {(e.action, e.outcome) for e in audit.items}
    assert ("jobs:run", "ok") in actions and ("jobs:run", "denied") in actions


def test_jobs_need_the_activity_log():
    from resource_engine.errors import ConfigurationError

    app, manager, hub, _ = build()
    with pytest.raises(ConfigurationError):
        mount_explorer(FastAPI(), manager, jobs=True)
