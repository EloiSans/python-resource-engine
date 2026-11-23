from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aio_pika


@dataclass(frozen=True)
class Topology:
    exchange: aio_pika.abc.AbstractExchange
    queue: aio_pika.abc.AbstractQueue
    dead_letter_queue: aio_pika.abc.AbstractQueue | None


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
    *,
    exchange: str,
    queue: str,
    binding_keys: Sequence[str] = ("#",),
    dead_letter: bool = True,
    delivery_limit: int | None = None,
) -> Topology:
    """Durable topic exchange, quorum queue bound to it and (optionally) a DLQ.

    Routing keys are ``<resource>.<EventType>`` (see ``default_routing_key``), so
    ``binding_keys=["invoices.*"]`` subscribes to every invoice event. Rejected
    messages go to ``<queue>.dlq`` through the ``<exchange>.dlx`` exchange.
    ``delivery_limit`` sets the quorum queue's own redelivery limit (the broker's
    safety net; RabbitMQ 4 defaults to 20); keep it above the consumer's
    ``RetryPolicy.max_deliveries``.
    """
    events = await channel.declare_exchange(exchange, aio_pika.ExchangeType.TOPIC, durable=True)
    arguments: dict[str, Any] = {"x-queue-type": "quorum"}
    dlq = None
    if dead_letter:
        dlx = await channel.declare_exchange(
            f"{exchange}.dlx", aio_pika.ExchangeType.DIRECT, durable=True
        )
        dlq = await channel.declare_queue(
            f"{queue}.dlq", durable=True, arguments={"x-queue-type": "quorum"}
        )
        await dlq.bind(dlx, routing_key=queue)
        arguments["x-dead-letter-exchange"] = dlx.name
        arguments["x-dead-letter-routing-key"] = queue
    if delivery_limit is not None:
        arguments["x-delivery-limit"] = delivery_limit
    main = await channel.declare_queue(queue, durable=True, arguments=arguments)
    for key in binding_keys:
        await main.bind(events, routing_key=key)
    return Topology(events, main, dlq)
