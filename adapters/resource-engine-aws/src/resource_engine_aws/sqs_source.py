"""SQS queue as a ``MessageSource`` (pull consumer)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from resource_engine.errors import UndecodableMessage
from resource_engine.messaging import EventEnvelope, try_decode
from resource_engine.messaging.inbound import envelope_from_json
from resource_engine.ports.messaging import ReceivedMessage

#: SQS limits for ReceiveMessage and visibility timeouts.
MAX_RECEIVE = 10
MAX_WAIT_SECONDS = 20
MAX_VISIBILITY_SECONDS = 12 * 60 * 60


def decode_sqs_body(body: str, *, type_prefix: str = "") -> EventEnvelope:
    """Envelope from an SQS body, unwrapping SNS notifications (non-raw delivery)."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise UndecodableMessage(f"SQS body is not JSON: {exc}") from exc
    if isinstance(data, dict) and data.get("Type") == "Notification" and "Message" in data:
        try:
            data = json.loads(data["Message"])
        except ValueError as exc:
            raise UndecodableMessage(f"SNS message is not JSON: {exc}") from exc
    return envelope_from_json(data, type_prefix=type_prefix)


class SqsMessageSource:
    """``MessageSource`` reading an SQS queue (standard or FIFO).

    Args:
        client: aiobotocore SQS client, owned by the caller.
        queue_url: Queue to read.
        type_prefix: CloudEvents type prefix to strip, if producers use one.

    ``retry`` changes the message visibility to the delay, so it reappears then.
    There is no explicit dead-letter call: configure a redrive policy
    (``maxReceiveCount``) on the queue, and keep it at least as high as the
    consumer's ``RetryPolicy.max_deliveries``. Long polling is used for waits.
    """

    def __init__(self, client: Any, queue_url: str, *, type_prefix: str = "") -> None:
        self.client = client
        self.queue_url = queue_url
        self.type_prefix = type_prefix

    def _decode(self, raw: dict[str, Any]) -> ReceivedMessage:
        count = raw.get("Attributes", {}).get("ApproximateReceiveCount")
        return try_decode(
            lambda: decode_sqs_body(raw["Body"], type_prefix=self.type_prefix),
            handle=raw["ReceiptHandle"],
            delivery_count=int(count) if count else None,
            message_id=raw.get("MessageId"),
        )

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        response = await self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max(1, min(max_messages, MAX_RECEIVE)),
            WaitTimeSeconds=int(min(max(wait_seconds, 0), MAX_WAIT_SECONDS)),
            AttributeNames=["ApproximateReceiveCount"],
            MessageAttributeNames=["All"],
        )
        return [self._decode(raw) for raw in response.get("Messages", [])]

    async def _batched(self, op: str, messages: Sequence[ReceivedMessage], **extra: Any) -> None:
        for start in range(0, len(messages), MAX_RECEIVE):
            entries = [
                {"Id": str(n), "ReceiptHandle": m.handle, **extra}
                for n, m in enumerate(messages[start : start + MAX_RECEIVE])
            ]
            response = await getattr(self.client, op)(QueueUrl=self.queue_url, Entries=entries)
            if response.get("Failed"):
                failed = response["Failed"][0]
                raise RuntimeError(
                    f"SQS {op} failed for {len(response['Failed'])} messages: "
                    f"{failed.get('Code')}: {failed.get('Message')}"
                )

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        await self._batched("delete_message_batch", messages)

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        timeout = int(min(max(delay_seconds, 0), MAX_VISIBILITY_SECONDS))
        await self._batched("change_message_visibility_batch", messages, VisibilityTimeout=timeout)
