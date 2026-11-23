from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope

from resource_engine_aws._batching import chunk, string_attributes

type GroupKey = Callable[[EventEnvelope], str]


def default_group(envelope: EventEnvelope) -> str:
    return envelope.key or envelope.resource or "default"


class SqsMessagePublisher:
    """``MessagePublisher`` that sends envelopes to an SQS queue.

    Args:
        client: An aiobotocore SQS client (``session.create_client("sqs")``),
            owned and closed by the caller.
        queue_url: Destination queue URL.
        fifo: FIFO queue mode; detected from a ``.fifo`` URL suffix by default.
            FIFO messages use ``group_key`` as ``MessageGroupId`` and the envelope id
            as ``MessageDeduplicationId``.
        group_key: Message group for FIFO queues (default: the envelope key,
            i.e. one group per entity).

    The body is the envelope JSON; event type, id, resource and correlation id are
    also sent as message attributes.
    """

    def __init__(
        self,
        client: Any,
        queue_url: str,
        *,
        fifo: bool | None = None,
        group_key: GroupKey = default_group,
    ) -> None:
        self.client = client
        self.queue_url = queue_url
        self.fifo = queue_url.endswith(".fifo") if fifo is None else fifo
        self.group_key = group_key

    def _entry(self, env: EventEnvelope) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "Id": env.id,
            "MessageBody": env.to_json(),
            "MessageAttributes": string_attributes(env.attributes()),
        }
        if self.fifo:
            entry["MessageGroupId"] = self.group_key(env)
            entry["MessageDeduplicationId"] = env.id
        return entry

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        failed: list[str] = []
        errors: list[str] = []
        for batch in chunk([self._entry(m) for m in messages], "MessageBody"):
            try:
                response = await self.client.send_message_batch(
                    QueueUrl=self.queue_url, Entries=batch
                )
            except Exception as exc:  # the whole batch was not accepted
                failed += [e["Id"] for e in batch]
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            for item in response.get("Failed", []):
                failed.append(item["Id"])
                errors.append(f"{item.get('Code')}: {item.get('Message')}")
        if failed:
            raise PublishFailed(
                f"SQS rejected {len(failed)} of {len(messages)} messages: {'; '.join(errors[:3])}",
                failed_ids=failed,
            )
