"""Service Bus source with a fake receiver (the emulator needs Microsoft EULAs)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from resource_engine.messaging import MessageConsumer, MessageProcessor, RetryPolicy
from resource_engine.testing.contracts import MessageSourceContract, contract_envelope
from resource_engine_azure import ServiceBusMessageSource


@dataclass(eq=False)
class FakeMessage:
    sections: list[bytes]
    message_id: str
    delivery_count: int = 0
    locked: bool = False

    @property
    def body(self):
        return iter(self.sections)


@dataclass
class FakeReceiver:
    queue: list[FakeMessage] = field(default_factory=list)
    dead: list[tuple[FakeMessage, str]] = field(default_factory=list)

    def send(self, body: bytes, message_id: str) -> None:
        self.queue.append(FakeMessage([body[:10], body[10:]], message_id))

    async def receive_messages(self, max_message_count, max_wait_time):
        batch = [m for m in self.queue if not m.locked][:max_message_count]
        for m in batch:
            m.locked = True
            m.delivery_count += 1
        return batch

    async def complete_message(self, m):
        self.queue.remove(m)

    async def abandon_message(self, m):
        m.locked = False

    async def dead_letter_message(self, m, reason, error_description):
        self.queue.remove(m)
        self.dead.append((m, reason))


class TestServiceBusSource(MessageSourceContract):
    @pytest.fixture
    def receiver(self):
        return FakeReceiver()

    @pytest.fixture
    def message_source(self, receiver):
        return ServiceBusMessageSource(receiver)

    @pytest.fixture
    def send(self, receiver):
        async def send(envelopes):
            for env in envelopes:
                receiver.send(env.to_json().encode(), env.id)

        return send


async def test_poison_and_rejected_messages_go_to_the_dead_letter_queue():
    receiver = FakeReceiver()
    receiver.send(b"definitely not json", "poison")
    receiver.send(contract_envelope(1).to_json().encode(), "fails")

    def handler(env):
        raise RuntimeError("bug")

    consumer = MessageConsumer(
        ServiceBusMessageSource(receiver),
        MessageProcessor(handler, name="az", retry=RetryPolicy(max_deliveries=2, base_delay=0)),
    )
    first = await consumer.run_once()
    assert first.dead_lettered == 1 and first.retried == 1
    second = await consumer.run_once()
    assert second.dead_lettered == 1
    assert [m.message_id for m, _ in receiver.dead] == ["poison", "fails"]
    assert receiver.dead[1][1] == "RuntimeError: bug" and receiver.queue == []
