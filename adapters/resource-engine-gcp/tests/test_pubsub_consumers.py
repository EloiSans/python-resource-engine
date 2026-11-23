from __future__ import annotations

import base64
import json

import pytest
from resource_engine.messaging import EventRouter, MessageConsumer, MessageProcessor, RetryPolicy
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import MessageSourceContract, contract_envelope, drain
from resource_engine_gcp import PubSubMessagePublisher, PubSubMessageSource, decode_pubsub_push


@pytest.mark.emulator("pubsub")
class TestPubSubSource(MessageSourceContract):
    @pytest.fixture
    def message_source(self, clients, topology):
        return PubSubMessageSource(clients[1], topology[1])

    @pytest.fixture
    def send(self, clients, topology):
        return PubSubMessagePublisher(clients[0], topology[0], ordering=True).publish


@pytest.mark.emulator("pubsub")
async def test_consumer_round_trip_with_retry(clients, topology):
    await PubSubMessagePublisher(clients[0], topology[0]).publish([contract_envelope(1)])
    attempts = {"n": 0}
    router = EventRouter()

    @router.on("ContractEvent")
    def handle(env):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("busy")

    consumer = MessageConsumer(
        PubSubMessageSource(clients[1], topology[1]),
        MessageProcessor(
            router, name="gcp", inbox=InMemoryStore().unit_of_work, retry=RetryPolicy(base_delay=0)
        ),
        wait_seconds=5,
    )
    first = await consumer.run_once()
    assert first.retried == 1
    total = 0
    for _ in range(5):
        total += (await consumer.run_once()).acked
        if total:
            break
    assert total == 1 and attempts["n"] == 2
    assert await drain(PubSubMessageSource(clients[1], topology[1]), 1, timeout=2) == []


def test_push_decoder():
    env = contract_envelope(3)
    body = json.dumps(
        {
            "message": {
                "data": base64.b64encode(env.to_json().encode()).decode(),
                "messageId": "9",
            },
            "subscription": "projects/p/subscriptions/s",
            "deliveryAttempt": 2,
        }
    ).encode()
    received = decode_pubsub_push({}, body)
    assert received.envelope == env and received.delivery_count == 2
    assert received.message_id == "9"
    assert decode_pubsub_push({}, b'{"message": {"data": "%%%"}}').envelope is None
