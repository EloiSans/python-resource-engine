from __future__ import annotations

import json

import pytest
from resource_engine.testing.contracts import MessagePublisherContract, contract_envelope
from resource_engine_aws import SnsMessagePublisher, SqsMessagePublisher
from resource_engine_aws._batching import MAX_BATCH_BYTES, chunk

pytestmark = pytest.mark.emulator("aws")


class TestSqsStandard(MessagePublisherContract):
    @pytest.fixture
    async def queue_url(self, aws, unique):
        sqs, _ = aws
        return (await sqs.create_queue(QueueName=unique("re-std")))["QueueUrl"]

    @pytest.fixture
    def message_publisher(self, aws, queue_url):
        return SqsMessagePublisher(aws[0], queue_url)

    @pytest.fixture
    def receive(self, aws, queue_url, receiver):
        return receiver(aws[0], queue_url)

    async def test_message_attributes(self, aws, queue_url, message_publisher):
        env = contract_envelope(1)
        await message_publisher.publish([env])
        resp = await aws[0].receive_message(
            QueueUrl=queue_url, WaitTimeSeconds=2, MessageAttributeNames=["All"]
        )
        attrs = {k: v["StringValue"] for k, v in resp["Messages"][0]["MessageAttributes"].items()}
        assert attrs["event_type"] == "ContractEvent" and attrs["event_id"] == env.id


class TestSqsFifo(MessagePublisherContract):
    @pytest.fixture
    async def queue_url(self, aws, unique):
        resp = await aws[0].create_queue(
            QueueName=unique("re-fifo") + ".fifo", Attributes={"FifoQueue": "true"}
        )
        return resp["QueueUrl"]

    @pytest.fixture
    def message_publisher(self, aws, queue_url):
        return SqsMessagePublisher(aws[0], queue_url)

    @pytest.fixture
    def receive(self, aws, queue_url, receiver):
        return receiver(aws[0], queue_url)

    async def test_duplicate_ids_are_dropped(self, message_publisher, receive):
        env = contract_envelope(3)
        await message_publisher.publish([env])
        await message_publisher.publish([env])
        assert len(await receive(2, timeout=4)) == 1


class TestSnsToSqs(MessagePublisherContract):
    @pytest.fixture
    async def topology(self, aws, unique):
        sqs, sns = aws
        topic_arn = (await sns.create_topic(Name=unique("re-topic")))["TopicArn"]
        queue_url = (await sqs.create_queue(QueueName=unique("re-sub")))["QueueUrl"]
        queue_arn = (
            await sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
        )["Attributes"]["QueueArn"]
        # As on AWS, the queue policy must let the topic send to it.
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "sns.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
                }
            ],
        }
        await sqs.set_queue_attributes(
            QueueUrl=queue_url, Attributes={"Policy": json.dumps(policy)}
        )
        await sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=queue_arn,
            Attributes={"RawMessageDelivery": "true"},
        )
        return topic_arn, queue_url

    @pytest.fixture
    def message_publisher(self, aws, topology):
        return SnsMessagePublisher(aws[1], topology[0])

    @pytest.fixture
    def receive(self, aws, topology, receiver):
        return receiver(aws[0], topology[1])


def test_chunking_respects_count_and_size():
    small = [{"Id": str(i), "Message": "x"} for i in range(23)]
    assert [len(b) for b in chunk(small, "Message")] == [10, 10, 3]
    big = [{"Id": str(i), "Message": "x" * (MAX_BATCH_BYTES // 2)} for i in range(3)]
    assert [len(b) for b in chunk(big, "Message")] == [2, 1]
    with pytest.raises(ValueError):
        list(chunk([{"Id": "1", "Message": "x" * (MAX_BATCH_BYTES + 1)}], "Message"))


async def test_outbox_relay_to_sqs_end_to_end(aws, unique, receiver):
    from invoices.bootstrap import registry
    from resource_engine import ResourceManager
    from resource_engine.messaging import OutboxDelivery, OutboxRelay
    from resource_engine.testing import InMemoryBlobStore, InMemoryStore

    sqs, _ = aws
    queue_url = (await sqs.create_queue(QueueName=unique("re-outbox")))["QueueUrl"]
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")

    relay = OutboxRelay(store.unit_of_work, SqsMessagePublisher(sqs, queue_url))
    assert await relay.run_once() == 2
    got = await receiver(sqs, queue_url)(2)
    assert sorted(e.type for e in got) == ["ResourceCreated", "TransitionExecuted"]
    assert {e.key for e in got} == {f"invoices:{invoice.id}"}
