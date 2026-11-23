"""Pub/Sub publisher against the official emulator (see compose.emulators.yaml)."""

from __future__ import annotations

import asyncio

import pytest
from resource_engine.messaging import EventEnvelope
from resource_engine.testing.contracts import MessagePublisherContract, contract_envelope
from resource_engine_gcp import PubSubMessagePublisher

pytestmark = pytest.mark.emulator("pubsub")


def make_receive(subscriber, subscription):
    async def receive(expected: int, timeout: float = 15.0) -> list[EventEnvelope]:
        got: dict[str, EventEnvelope] = {}
        attrs: dict[str, dict[str, str]] = {}
        deadline = asyncio.get_running_loop().time() + timeout
        while len(got) < expected and asyncio.get_running_loop().time() < deadline:
            resp = await asyncio.to_thread(
                subscriber.pull,
                request={"subscription": subscription, "max_messages": 50},
                timeout=5,
            )
            if not resp.received_messages:
                await asyncio.sleep(0.2)
                continue
            for msg in resp.received_messages:
                env = EventEnvelope.from_json(msg.message.data)
                got[env.id] = env
                attrs[env.id] = dict(msg.message.attributes)
            await asyncio.to_thread(
                subscriber.acknowledge,
                request={
                    "subscription": subscription,
                    "ack_ids": [m.ack_id for m in resp.received_messages],
                },
            )
        receive.attributes = attrs  # type: ignore[attr-defined]
        return list(got.values())

    return receive


class TestPubSub(MessagePublisherContract):
    @pytest.fixture
    def message_publisher(self, clients, topology):
        return PubSubMessagePublisher(clients[0], topology[0], ordering=True)

    @pytest.fixture
    def receive(self, clients, topology):
        return make_receive(clients[1], topology[1])

    async def test_attributes_and_ordering_key(self, message_publisher, receive):
        env = contract_envelope(5)
        await message_publisher.publish([env])
        await receive(1)
        assert receive.attributes[env.id]["event_type"] == "ContractEvent"
        assert receive.attributes[env.id]["resource"] == env.resource
