from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope


class ServiceBusMessagePublisher:
    """``MessagePublisher`` for an Azure Service Bus queue or topic.

    Args:
        sender: An async ``ServiceBusSender``
            (``client.get_queue_sender(...)`` / ``client.get_topic_sender(...)``),
            owned and closed by the caller.
        sessions: Set ``session_id`` to the envelope key, for session-enabled
            entities that need per-entity ordering.

    Each message has ``message_id`` = envelope id (enable duplicate detection on
    the entity to drop resends), ``subject`` = event type, ``content_type`` =
    ``application/json`` and the envelope attributes as application properties.
    """

    def __init__(self, sender: Any, *, sessions: bool = False) -> None:
        self.sender = sender
        self.sessions = sessions

    def _message(self, env: EventEnvelope) -> Any:
        from azure.servicebus import ServiceBusMessage

        return ServiceBusMessage(
            env.to_json(),
            message_id=env.id,
            content_type="application/json",
            subject=env.type,
            correlation_id=env.correlation_id,
            session_id=(env.key or "default") if self.sessions else None,
            application_properties=dict(env.attributes()),
        )

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        if not messages:
            return
        pending = list(messages)
        sent = 0
        try:
            while pending:
                batch = await self.sender.create_message_batch()
                in_batch: list[EventEnvelope] = []
                for env in pending:
                    try:
                        batch.add_message(self._message(env))
                    except ValueError:  # MessageSizeExceededError: batch is full
                        if not in_batch:
                            raise
                        break
                    in_batch.append(env)
                await self.sender.send_messages(batch)
                sent += len(in_batch)
                pending = pending[len(in_batch) :]
        except Exception as exc:
            raise PublishFailed(
                f"Service Bus accepted {sent} of {len(messages)} messages: "
                f"{type(exc).__name__}: {exc}",
                failed_ids=[env.id for env in pending],
            ) from exc
