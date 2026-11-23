"""SAP publishers against recorded HTTP exchanges (no public emulator exists)."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope, from_cloudevent
from resource_engine.testing.contracts import MessagePublisherContract, contract_envelope
from resource_engine_sap import (
    AdvancedEventMeshPublisher,
    EventMeshMessagePublisher,
    event_mesh_client,
    event_topic,
)
from resource_engine_sap.advanced_event_mesh import event_topic as aem_topic

SERVICE_KEY = {
    "namespace": "acme/billing/1",
    "messaging": [
        {"protocol": ["amqp10ws"], "uri": "wss://ignored"},
        {
            "protocol": ["httprest"],
            "broker": {"type": "saprestmgw"},
            "uri": "https://em.example",
            "oa2": {
                "clientid": "cid",
                "clientsecret": "secret",
                "tokenendpoint": "https://uaa.example/oauth/token",
                "granttype": "client_credentials",
            },
        },
    ],
}


class FakeEventMesh:
    def __init__(self, reject_topics: set[str] = frozenset()) -> None:
        self.messages: list[httpx.Request] = []
        self.tokens = 0
        self.reject_topics = reject_topics

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "uaa.example":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 43199})
        assert request.headers["authorization"] == "Bearer tok"
        topic = unquote(request.url.raw_path.decode().split("/")[4])
        if topic in self.reject_topics:
            return httpx.Response(400, json={"message": "invalid topic"})
        self.messages.append(request)
        return httpx.Response(204)


@pytest.fixture
def mesh():
    return FakeEventMesh()


@pytest.fixture
def mesh_client(mesh):
    return event_mesh_client(SERVICE_KEY, transport=httpx.MockTransport(mesh))


class TestEventMeshPublisher(MessagePublisherContract):
    @pytest.fixture
    def message_publisher(self, mesh_client):
        return EventMeshMessagePublisher(mesh_client, topic=event_topic("acme/billing/1"))

    @pytest.fixture
    def receive(self, mesh):
        async def receive(n: int) -> list[EventEnvelope]:
            return [from_cloudevent(r.content) for r in mesh.messages[:n]]

        return receive


async def test_event_mesh_request_shape(mesh, mesh_client):
    publisher = EventMeshMessagePublisher(
        mesh_client, topic=event_topic(SERVICE_KEY["namespace"]), type_prefix="acme.billing."
    )
    await publisher.publish([contract_envelope(1), contract_envelope(2)])
    request = mesh.messages[0]
    assert request.url.raw_path == (
        b"/messagingrest/v1/topics/acme%2Fbilling%2F1%2Fcontract_items%2FContractEvent/messages"
    )
    assert request.headers["x-qos"] == "1"
    assert request.headers["content-type"].startswith("application/cloudevents+json")
    assert json.loads(request.content)["type"] == "acme.billing.ContractEvent"
    assert mesh.tokens == 1  # token cached across messages


async def test_event_mesh_queue_and_envelope_format(mesh, mesh_client):
    publisher = EventMeshMessagePublisher(
        mesh_client, queue="acme/billing/1/invoices", qos=0, format="envelope"
    )
    env = contract_envelope(3)
    await publisher.publish([env])
    request = mesh.messages[0]
    assert request.url.raw_path.startswith(
        b"/messagingrest/v1/queues/acme%2Fbilling%2F1%2Finvoices/"
    )
    assert request.headers["x-qos"] == "0"
    assert EventEnvelope.from_json(request.content) == env


async def test_event_mesh_reports_rejected_messages(mesh_client):
    mesh = FakeEventMesh(reject_topics={"bad"})
    client = event_mesh_client(SERVICE_KEY, transport=httpx.MockTransport(mesh))
    good, bad = contract_envelope(1), contract_envelope(2, type="Bad")
    publisher = EventMeshMessagePublisher(
        client, topic=lambda env: "bad" if env.type == "Bad" else "ok"
    )
    with pytest.raises(PublishFailed) as info:
        await publisher.publish([good, bad])
    assert info.value.failed_ids == [bad.id]


def test_event_mesh_validates_arguments(mesh_client):
    with pytest.raises(ValueError):
        EventMeshMessagePublisher(mesh_client)
    with pytest.raises(ValueError):
        EventMeshMessagePublisher(mesh_client, topic="t", queue="q")
    with pytest.raises(ValueError):
        event_mesh_client({"messaging": []})


class FakeSolace:
    def __init__(self) -> None:
        self.messages: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        self.messages.append(request)
        return httpx.Response(200)


async def test_advanced_event_mesh_request_shape():
    solace = FakeSolace()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(solace),
        base_url="https://aem.example:9443",
        auth=httpx.BasicAuth("user", "pass"),
    )
    publisher = AdvancedEventMeshPublisher(
        client, topic=aem_topic("acme/billing"), time_to_live_ms=60000
    )
    env = contract_envelope(5, correlation_id="corr 5")
    await publisher.publish([env])
    (request,) = solace.messages
    assert request.url.path == "/TOPIC/acme/billing/contract_items/ContractEvent/5"
    headers = request.headers
    assert headers["solace-message-id"] == env.id
    assert headers["solace-correlation-id"] == "corr%205"
    assert headers["solace-delivery-mode"] == "Persistent"
    assert headers["solace-time-to-live-in-ms"] == "60000"
    assert headers["solace-user-property-event_type"] == "ContractEvent"
    assert headers["solace-user-property-jmsxgroupid"] == "contract_items%3A5"
    assert "idempotency-key" not in headers
    assert EventEnvelope.from_json(request.content) == env
