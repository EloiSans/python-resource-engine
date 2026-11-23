from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope


class PubSubMessagePublisher:
    """``MessagePublisher`` for a Google Cloud Pub/Sub topic.

    Args:
        client: A ``google.cloud.pubsub_v1.PublisherClient`` (it batches internally).
            Create it with ``PublisherOptions(enable_message_ordering=True)`` when
            ``ordering`` is used.
        topic_path: ``projects/<project>/topics/<topic>``.
        ordering: Use the envelope key as ``ordering_key`` (per-entity order).

    Data is the envelope JSON; envelope attributes become message attributes.
    Honors ``PUBSUB_EMULATOR_HOST`` through the client.
    """

    def __init__(self, client: Any, topic_path: str, *, ordering: bool = False) -> None:
        self.client = client
        self.topic_path = topic_path
        self.ordering = ordering

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        if not messages:
            return
        futures = []
        for env in messages:
            future = self.client.publish(
                self.topic_path,
                env.to_json().encode(),
                ordering_key=(env.key or "") if self.ordering else "",
                **env.attributes(),
            )
            futures.append(asyncio.wrap_future(future))
        results = await asyncio.gather(*futures, return_exceptions=True)
        failed = [
            (env, r) for env, r in zip(messages, results, strict=True) if isinstance(r, Exception)
        ]
        if not failed:
            return
        if self.ordering:  # a failed ordering key stays paused until resumed
            for key in {env.key for env, _ in failed if env.key}:
                self.client.resume_publish(self.topic_path, key)
        first = failed[0][1]
        raise PublishFailed(
            f"Pub/Sub rejected {len(failed)} of {len(messages)} messages: "
            f"{type(first).__name__}: {first}",
            failed_ids=[env.id for env, _ in failed],
        )
