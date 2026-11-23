from __future__ import annotations

import json

import httpx
import pytest
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope, from_cloudevent
from resource_engine.testing.contracts import MessagePublisherContract, contract_envelope
from resource_engine_http import WebhookMessagePublisher


class Receiver:
    """Fake webhook endpoint that records bodies and can reject chosen ids."""

    def __init__(self, reject: set[str] = frozenset()) -> None:
        self.requests: list[httpx.Request] = []
        self.reject = reject

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(500 if request.headers["idempotency-key"] in self.reject else 202)


def client_for(receiver: Receiver) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(receiver), base_url="http://hooks")


class TestWebhookPublisher(MessagePublisherContract):
    @pytest.fixture
    def receiver(self):
        return Receiver()

    @pytest.fixture
    def message_publisher(self, receiver):
        return WebhookMessagePublisher(client_for(receiver), "/events")

    @pytest.fixture
    def receive(self, receiver):
        async def receive(n: int) -> list[EventEnvelope]:
            return [EventEnvelope.from_json(r.content) for r in receiver.requests[:n]]

        return receive


async def test_failed_deliveries_are_reported_by_id():
    envs = [contract_envelope(n) for n in range(3)]
    receiver = Receiver(reject={envs[1].id})
    with pytest.raises(PublishFailed) as info:
        await WebhookMessagePublisher(client_for(receiver), "/events").publish(envs)
    assert info.value.failed_ids == [envs[1].id]
    request = receiver.requests[0]
    assert request.headers["x-event-event-type"] == "ContractEvent"


async def test_cloudevents_format_is_lossless():
    receiver = Receiver()
    publisher = WebhookMessagePublisher(
        client_for(receiver), "/events", format="cloudevents", source="/tests", type_prefix="re."
    )
    env = contract_envelope(4)
    await publisher.publish([env])
    request = receiver.requests[0]
    assert request.headers["content-type"].startswith("application/cloudevents+json")
    body = json.loads(request.content)
    assert body["specversion"] == "1.0" and body["type"] == "re.ContractEvent"
    assert from_cloudevent(body, type_prefix="re.") == env


def test_cloudevents_needs_a_source():
    with pytest.raises(ValueError):
        WebhookMessagePublisher(httpx.AsyncClient(), "/e", format="cloudevents")
