from __future__ import annotations

import asyncio

import pytest
from resource_engine.errors import MessageRejected, PublishFailed
from resource_engine.messaging import EventEnvelope, MessageConsumer, MessageProcessor, RetryPolicy
from resource_engine.testing.contracts import (
    MessagePublisherContract,
    MessageSourceContract,
    contract_envelope,
    drain,
)
from resource_engine_rabbitmq import RabbitMqMessagePublisher, RabbitMqMessageSource

pytestmark = pytest.mark.emulator("rabbitmq")


async def _get_all(queue, expected: int, timeout: float = 10.0) -> list:
    got = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(got) < expected and asyncio.get_running_loop().time() < deadline:
        message = await queue.get(no_ack=True, fail=False)
        if message is None:
            await asyncio.sleep(0.05)
            continue
        got.append(message)
    return got


class TestRabbitMqPublisher(MessagePublisherContract):
    @pytest.fixture
    def message_publisher(self, topology):
        return RabbitMqMessagePublisher(topology.exchange)

    @pytest.fixture
    def receive(self, topology):
        async def receive(n: int) -> list[EventEnvelope]:
            return [EventEnvelope.from_json(m.body) for m in await _get_all(topology.queue, n)]

        return receive


class TestRabbitMqSource(MessageSourceContract):
    @pytest.fixture
    async def message_source(self, topology):
        source = RabbitMqMessageSource(topology.queue)
        yield source
        await source.close()

    @pytest.fixture
    def send(self, topology):
        return RabbitMqMessagePublisher(topology.exchange).publish


async def test_unroutable_messages_fail(channel):
    exchange = await channel.declare_exchange("re-test-unbound", "topic", auto_delete=True)
    with pytest.raises(PublishFailed) as info:
        await RabbitMqMessagePublisher(exchange).publish([contract_envelope(1)])
    assert len(info.value.failed_ids) == 1


async def test_consumer_dead_letters_to_the_dlq(topology):
    await RabbitMqMessagePublisher(topology.exchange).publish(
        [contract_envelope(1), contract_envelope(2)]
    )

    def handler(env):
        if env.payload["n"] == 2:
            raise MessageRejected("invalid")

    source = RabbitMqMessageSource(topology.queue)
    consumer = MessageConsumer(
        source,
        MessageProcessor(handler, name="rmq", retry=RetryPolicy(base_delay=0)),
        wait_seconds=2,
    )
    stats = await consumer.run_once()
    while stats.received < 2:
        stats.add(await consumer.run_once())
    await source.close()
    assert stats.acked == 1 and stats.dead_lettered == 1
    (dead,) = await _get_all(topology.dead_letter_queue, 1)
    assert EventEnvelope.from_json(dead.body).payload["n"] == 2


async def test_delivery_count_from_quorum_queue(topology):
    await RabbitMqMessagePublisher(topology.exchange).publish([contract_envelope(5)])
    source = RabbitMqMessageSource(topology.queue)
    (first,) = await drain(source, 1)
    await source.retry([first], 0)
    (second,) = await drain(source, 1)
    await source.ack([second])
    await source.close()
    assert (first.delivery_count, second.delivery_count) == (1, 2)
