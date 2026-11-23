from __future__ import annotations

import json

import httpx
import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from resource_engine.errors import MessageRejected
from resource_engine.messaging import EventRouter, MessageProcessor, RetryPolicy, to_cloudevent
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import contract_envelope
from resource_engine_fastapi import build_event_receiver


def require_token(x_token: str = Header()) -> None:
    if x_token != "s3cret":
        raise HTTPException(401)


@pytest.fixture
def state():
    return {"seen": [], "fail": False}


@pytest.fixture
def client(state):
    router = EventRouter()

    @router.on("ContractEvent")
    async def handle(env):
        if state["fail"] == "reject":
            raise MessageRejected("bad order")
        if state["fail"]:
            raise ConnectionError("busy")
        state["seen"].append(env.id)

    processor = MessageProcessor(
        router, name="hooks", inbox=InMemoryStore().unit_of_work, retry=RetryPolicy(base_delay=3)
    )
    app = FastAPI()
    app.include_router(
        build_event_receiver(
            processor, allowed_origins=["eventmesh.example"], dependencies=[Depends(require_token)]
        )
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://svc",
        headers={"x-token": "s3cret"},
    )


async def test_envelope_cloudevent_and_duplicate(client, state):
    env = contract_envelope(1)
    assert (await client.post("/events", content=env.to_json())).status_code == 204
    ce = to_cloudevent(contract_envelope(2), source="/t")
    r = await client.post(
        "/events", content=json.dumps(ce), headers={"content-type": "application/cloudevents+json"}
    )
    assert r.status_code == 204
    assert (await client.post("/events", content=env.to_json())).status_code == 204
    assert len(state["seen"]) == 2  # the repeated delivery was skipped


async def test_retry_and_dead_letter_statuses(client, state):
    state["fail"] = True
    r = await client.post("/events", content=contract_envelope(3).to_json())
    assert r.status_code == 503 and r.headers["retry-after"] == "3"
    state["fail"] = "reject"
    r = await client.post("/events", content=contract_envelope(4).to_json())
    assert r.status_code == 422 and r.json()["error"]["outcome"] == "dead_letter"
    assert (await client.post("/events", content=b"garbage")).status_code == 422


async def test_auth_dependency_and_handshake(client):
    r = await client.post("/events", content=b"{}", headers={"x-token": "nope"})
    assert r.status_code == 401
    ok = await client.options("/events", headers={"WebHook-Request-Origin": "eventmesh.example"})
    assert ok.status_code == 200
    assert ok.headers["webhook-allowed-origin"] == "eventmesh.example"
    denied = await client.options("/events", headers={"WebHook-Request-Origin": "evil.example"})
    assert denied.status_code == 403
