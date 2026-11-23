"""Service Bus publisher with a fake sender.

The official emulator needs Microsoft EULAs to be accepted (emulator + SQL
Server), so it is not part of the default test setup yet.
"""

from __future__ import annotations

import pytest
from resource_engine.errors import PublishFailed
from resource_engine.testing.contracts import contract_envelope
from resource_engine_azure import ServiceBusMessagePublisher


class FakeBatch:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.messages: list = []

    def add_message(self, message) -> None:
        if len(self.messages) == self.capacity:
            raise ValueError("MessageSizeExceededError")
        self.messages.append(message)


class FakeSender:
    def __init__(self, capacity: int = 10, fail_on_batch: int | None = None) -> None:
        self.capacity = capacity
        self.fail_on_batch = fail_on_batch
        self.sent_batches: list[list] = []

    async def create_message_batch(self) -> FakeBatch:
        return FakeBatch(self.capacity)

    async def send_messages(self, batch: FakeBatch) -> None:
        if self.fail_on_batch == len(self.sent_batches):
            raise ConnectionError("link detached")
        self.sent_batches.append(batch.messages)


async def test_messages_carry_envelope_metadata():
    sender = FakeSender()
    env = contract_envelope(1)
    await ServiceBusMessagePublisher(sender, sessions=True).publish([env])
    (msg,) = sender.sent_batches[0]
    assert msg.message_id == env.id
    assert msg.subject == "ContractEvent"
    assert msg.content_type == "application/json"
    assert msg.session_id == env.key
    assert msg.correlation_id == env.correlation_id
    assert msg.application_properties["event_type"] == "ContractEvent"
    assert str(msg) == env.to_json()


async def test_splits_when_batch_is_full():
    sender = FakeSender(capacity=4)
    await ServiceBusMessagePublisher(sender).publish([contract_envelope(n) for n in range(10)])
    assert [len(b) for b in sender.sent_batches] == [4, 4, 2]


async def test_failure_reports_only_unsent_messages():
    sender = FakeSender(capacity=4, fail_on_batch=1)
    envelopes = [contract_envelope(n) for n in range(10)]
    with pytest.raises(PublishFailed) as info:
        await ServiceBusMessagePublisher(sender).publish(envelopes)
    assert info.value.failed_ids == [e.id for e in envelopes[4:]]


async def test_empty_publish_does_nothing():
    sender = FakeSender()
    await ServiceBusMessagePublisher(sender).publish([])
    assert sender.sent_batches == []
