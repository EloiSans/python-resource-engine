from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

import aio_pika
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope

type RoutingKey = Callable[[EventEnvelope], str]


def default_routing_key(envelope: EventEnvelope) -> str:
    """``<resource>.<EventType>``, e.g. ``invoices.TransitionExecuted``."""
    return f"{envelope.resource or 'events'}.{envelope.type}"


class RabbitMqMessagePublisher:
    """``MessagePublisher`` for a RabbitMQ exchange.

    Args:
        exchange: aio-pika exchange from a channel opened with
            ``connection.channel(publisher_confirms=True, on_return_raises=True)``,
            so ``publish`` returns only after the broker has taken responsibility
            for each message, and unroutable messages raise instead of vanishing.
        routing_key: Function of the envelope (default ``<resource>.<type>``).
        mandatory: Unroutable messages (no bound queue) count as failures
            instead of being dropped silently.

    Messages are persistent JSON envelopes; ``message_id`` is the envelope id and
    the envelope attributes travel as headers.
    """

    def __init__(
        self,
        exchange: aio_pika.abc.AbstractExchange,
        *,
        routing_key: RoutingKey = default_routing_key,
        mandatory: bool = True,
    ) -> None:
        self.exchange = exchange
        self.routing_key = routing_key
        self.mandatory = mandatory

    def _message(self, env: EventEnvelope) -> aio_pika.Message:
        return aio_pika.Message(
            body=env.to_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=env.id,
            correlation_id=env.correlation_id,
            type=env.type,
            timestamp=env.occurred_at,
            headers=dict(env.attributes()),
        )

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        if not messages:
            return
        results = await asyncio.gather(
            *(
                self.exchange.publish(
                    self._message(env), routing_key=self.routing_key(env), mandatory=self.mandatory
                )
                for env in messages
            ),
            return_exceptions=True,
        )
        failed = [
            (env, r) for env, r in zip(messages, results, strict=True) if isinstance(r, Exception)
        ]
        if failed:
            first = failed[0][1]
            raise PublishFailed(
                f"RabbitMQ did not confirm {len(failed)} of {len(messages)} messages: "
                f"{type(first).__name__}: {first}",
                failed_ids=[env.id for env, _ in failed],
            )
