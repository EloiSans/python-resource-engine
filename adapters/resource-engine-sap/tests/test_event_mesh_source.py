"""Event Mesh pull consumer against a fake queue speaking the REST API."""

from __future__ import annotations

import time
import uuid
from urllib.parse import unquote

import httpx
import pytest
from resource_engine.errors import RemoteCallFailed
from resource_engine.messaging import EventRouter, MessageConsumer, MessageProcessor
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import MessageSourceContract, contract_envelope
from resource_engine_sap import EventMeshMessagePublisher, EventMeshMessageSource, event_mesh_client

QUEUE = "acme/billing/1/invoices"
KEY = {
    "messaging": [
        {
            "protocol": ["httprest"],
            "uri": "https://em.example",
            "oa2": {
                "clientid": "c",
                "clientsecret": "s",
                "tokenendpoint": "https://uaa.example/oauth/token",
            },
        }
    ]
}


class FakeQueue:
    """Queue semantics of the REST API: consumed messages stay until acknowledged and
    come back after ``redelivery`` seconds."""

    def __init__(self, redelivery: float = 0.2) -> None:
        self.redelivery = redelivery
        self.messages: dict[str, dict] = {}
        self.acks: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "uaa.example":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        parts = request.url.raw_path.decode().split("/")
        assert unquote(parts[4]) == QUEUE and request.headers["x-qos"] == "1"
        tail = parts[5:]
        if tail == ["messages"]:  # publish
            mid = uuid.uuid4().hex
            self.messages[mid] = {
                "body": request.content,
                "type": request.headers["content-type"],
                "visible_at": 0.0,
            }
            return httpx.Response(204)
        if tail == ["messages", "consumption"]:
            now = time.monotonic()
            for mid, m in self.messages.items():
                if m["visible_at"] <= now:
                    m["visible_at"] = now + self.redelivery
                    return httpx.Response(
                        200,
                        content=m["body"],
                        headers={"x-message-id": mid, "content-type": m["type"]},
                    )
            return httpx.Response(204)
        if tail[0] == "messages" and tail[-1] == "acknowledgement":
            mid = unquote(tail[1])
            if mid not in self.messages:
                return httpx.Response(404)
            del self.messages[mid]
            self.acks.append(mid)
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeQueue()


@pytest.fixture
def client(fake):
    return event_mesh_client(KEY, transport=httpx.MockTransport(fake))


class TestEventMeshSource(MessageSourceContract):
    @pytest.fixture
    def message_source(self, client):
        return EventMeshMessageSource(client, QUEUE, poll_interval=0.05)

    @pytest.fixture
    def send(self, client):
        return EventMeshMessagePublisher(client, queue=QUEUE).publish


async def test_consumer_acks_by_message_id(client, fake):
    await EventMeshMessagePublisher(client, queue=QUEUE).publish(
        [contract_envelope(1), contract_envelope(2)]
    )
    seen = []
    router = EventRouter()
    router.register("ContractEvent", lambda env: seen.append(env.payload["n"]))
    consumer = MessageConsumer(
        EventMeshMessageSource(client, QUEUE, poll_interval=0.05),
        MessageProcessor(router, name="sap", inbox=InMemoryStore().unit_of_work),
        wait_seconds=0.2,
    )
    stats = await consumer.run_once()
    assert sorted(seen) == [1, 2] and stats.acked == 2
    assert fake.messages == {} and len(fake.acks) == 2


async def test_broker_errors_surface_as_remote_call_failed():
    def down(request):
        if request.url.host == "uaa.example":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(503, text="maintenance")

    client = event_mesh_client(KEY, transport=httpx.MockTransport(down))
    with pytest.raises(RemoteCallFailed) as info:
        await EventMeshMessageSource(client, QUEUE).receive(1, 0)
    assert info.value.details["status"] == 503
