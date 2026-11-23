from __future__ import annotations

import json

import pytest
from resource_engine.messaging import EventRouter, MessageConsumer, MessageProcessor, RetryPolicy
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import MessageSourceContract, contract_envelope, drain
from resource_engine_aws import SnsMessagePublisher, SqsMessagePublisher, SqsMessageSource

pytestmark = pytest.mark.emulator("aws")


@pytest.fixture
async def queue_url(aws, unique):
    return (await aws[0].create_queue(QueueName=unique("re-src")))["QueueUrl"]


class TestSqsSource(MessageSourceContract):
    @pytest.fixture
    def message_source(self, aws, queue_url):
        return SqsMessageSource(aws[0], queue_url)

    @pytest.fixture
    def send(self, aws, queue_url):
        return SqsMessagePublisher(aws[0], queue_url).publish


async def test_consumer_retries_then_redrive_policy_dead_letters(aws, unique):
    sqs = aws[0]
    dlq_url = (await sqs.create_queue(QueueName=unique("re-dlq")))["QueueUrl"]
    dlq_arn = (await sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"]))[
        "Attributes"
    ]["QueueArn"]
    redrive = json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "2"})
    url = (
        await sqs.create_queue(QueueName=unique("re-main"), Attributes={"RedrivePolicy": redrive})
    )["QueueUrl"]
    await SqsMessagePublisher(sqs, url).publish([contract_envelope(1), contract_envelope(2)])

    seen: list[int] = []
    router = EventRouter()

    @router.on("ContractEvent")
    def handle(env):
        if env.payload["n"] == 2:
            raise ConnectionError("downstream busy")
        seen.append(env.payload["n"])

    consumer = MessageConsumer(
        SqsMessageSource(sqs, url),
        MessageProcessor(
            router,
            name="aws-test",
            inbox=InMemoryStore().unit_of_work,
            retry=RetryPolicy(max_deliveries=5, base_delay=0),
        ),
        wait_seconds=2,
    )
    for _ in range(6):
        await consumer.run_once()
    assert seen == [1]

    # after maxReceiveCount deliveries SQS moved the failing message to the DLQ
    dead = await SqsMessageSource(sqs, dlq_url).receive(10, 2)
    assert [m.envelope.payload["n"] for m in dead] == [2]
    assert dead[0].delivery_count is not None


async def test_sns_notifications_are_unwrapped(aws, unique, queue_url):
    sqs, sns = aws
    topic_arn = (await sns.create_topic(Name=unique("re-src-topic")))["TopicArn"]
    queue_arn = (await sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"]))[
        "Attributes"
    ]["QueueArn"]
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
    await sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps(policy)})
    await sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)  # not raw
    env = contract_envelope(9)
    await SnsMessagePublisher(sns, topic_arn).publish([env])
    (got,) = await drain(SqsMessageSource(sqs, queue_url), 1, timeout=15)
    assert got.envelope == env
