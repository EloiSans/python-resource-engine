from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope

from resource_engine_aws._batching import chunk, string_attributes
from resource_engine_aws.sqs import GroupKey, default_group


class SnsMessagePublisher:
    """``MessagePublisher`` that publishes envelopes to an SNS topic.

    Args:
        client: An aiobotocore SNS client, owned and closed by the caller.
        topic_arn: Destination topic.
        fifo: FIFO topic mode; detected from a ``.fifo`` ARN suffix by default.
        group_key: Message group for FIFO topics (default: one group per entity).

    Message attributes (``event_type``, ``resource``...) allow subscription filter
    policies. Enable *raw message delivery* on SQS subscriptions so consumers get
    the envelope JSON instead of the SNS notification wrapper.
    """

    def __init__(
        self,
        client: Any,
        topic_arn: str,
        *,
        fifo: bool | None = None,
        group_key: GroupKey = default_group,
    ) -> None:
        self.client = client
        self.topic_arn = topic_arn
        self.fifo = topic_arn.endswith(".fifo") if fifo is None else fifo
        self.group_key = group_key

    def _entry(self, env: EventEnvelope) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "Id": env.id,
            "Message": env.to_json(),
            "MessageAttributes": string_attributes(env.attributes()),
        }
        if self.fifo:
            entry["MessageGroupId"] = self.group_key(env)
            entry["MessageDeduplicationId"] = env.id
        return entry

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        failed: list[str] = []
        errors: list[str] = []
        for batch in chunk([self._entry(m) for m in messages], "Message"):
            try:
                response = await self.client.publish_batch(
                    TopicArn=self.topic_arn, PublishBatchRequestEntries=batch
                )
            except Exception as exc:
                failed += [e["Id"] for e in batch]
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            for item in response.get("Failed", []):
                failed.append(item["Id"])
                errors.append(f"{item.get('Code')}: {item.get('Message')}")
        if failed:
            raise PublishFailed(
                f"SNS rejected {len(failed)} of {len(messages)} messages: {'; '.join(errors[:3])}",
                failed_ids=failed,
            )
