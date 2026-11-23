from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore
from resource_engine_fastapi import mount_resources
from statemachine import State, StateMachine


@dataclass
class Task:
    title: str
    status: str | None = None
    version: int | None = None
    id: int | None = None


class TaskMachine(StateMachine):
    open = State(initial=True)
    closed = State(final=True)
    close = open.to(closed)


class TaskIn(BaseModel):
    title: str


class TaskPatch(BaseModel):
    title: str | None = None
    version: int | None = None


class TaskOut(TaskIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    version: int


class TaskResource(Resource):
    model = Task
    schemas = {"create": TaskIn, "update": TaskPatch, "read": TaskOut}
    operations = ["list", "get", "create", "update", "delete"]
    state_machine = TaskMachine
    version_field = "version"


class StrictResource(TaskResource):
    name = "strict_tasks"
    require_version = True


@pytest.fixture
def client():
    registry = ResourceRegistry()
    registry.register(TaskResource)
    registry.register(StrictResource)
    store = InMemoryStore.from_registry(registry)
    app = FastAPI()
    mount_resources(app, registry, ResourceManager(registry, store.unit_of_work))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_etag_round_trip_and_stale_writes(client):
    r = await client.post("/tasks", json={"title": "a"})
    assert r.status_code == 201 and r.headers["etag"] == '"1"'
    assert (await client.get("/tasks/1")).headers["etag"] == '"1"'
    r = await client.patch("/tasks/1", json={"title": "b"}, headers={"If-Match": '"1"'})
    assert r.status_code == 200 and r.headers["etag"] == '"2"' and r.json()["version"] == 2
    stale = await client.patch("/tasks/1", json={"title": "c"}, headers={"If-Match": '"1"'})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "RE-E105"
    assert stale.json()["error"]["details"]["current"] == 2
    body = await client.patch("/tasks/1", json={"title": "c", "version": 1})
    assert body.status_code == 409
    r = await client.post("/tasks/1/transitions/close", headers={"If-Match": 'W/"1"'})
    assert r.status_code == 409
    r = await client.post("/tasks/1/transitions/close", headers={"If-Match": '"2"'})
    assert r.status_code == 200 and r.headers["etag"] == '"3"'
    assert (await client.delete("/tasks/1", headers={"If-Match": '"2"'})).status_code == 409
    assert (await client.delete("/tasks/1", headers={"If-Match": "*"})).status_code == 204


async def test_required_and_malformed_versions(client):
    await client.post("/strict_tasks", json={"title": "a"})
    r = await client.patch("/strict_tasks/1", json={"title": "b"})
    assert r.status_code == 428 and r.json()["error"]["code"] == "RE-E106"
    r = await client.patch("/strict_tasks/1", json={"title": "b"}, headers={"If-Match": "nope"})
    assert r.status_code == 400
    ok = await client.patch("/strict_tasks/1", json={"title": "b"}, headers={"If-Match": '"1"'})
    assert ok.status_code == 200


async def test_if_match_is_documented_on_versioned_writes(client):
    spec = (await client.get("/openapi.json")).json()
    params = spec["paths"]["/tasks/{entity_id}"]["patch"]["parameters"]
    assert any(p["name"] == "If-Match" and p["in"] == "header" for p in params)
