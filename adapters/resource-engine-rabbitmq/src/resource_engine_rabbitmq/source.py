from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence

import aio_pika
from resource_engine.messaging import decode_body, try_decode
from resource_engine.ports.messaging import ReceivedMessage


class RabbitMqMessageSource:
    """``MessageSource`` over a RabbitMQ queue (quorum queues recommended).

    Args:
        queue: aio-pika queue (``declare_topology(...).queue``).
        prefetch: Unacknowledged messages the broker may push to this consumer.
        type_prefix: CloudEvents type prefix to strip, if producers use one.

    - ``ack`` acknowledges; ``retry`` nacks with requeue (redelivered at once:
      the delay is not supported); ``dead_letter`` rejects without requeue, so the
      queue's dead-letter exchange receives it (``SupportsDeadLetter``).
    - Delivery counts come from quorum queues' ``x-acquired-count`` (RabbitMQ 4)
      or ``x-delivery-count`` header; classic queues only say whether a message
      was redelivered, so the count is unknown there.
    - Messages are pushed into an internal buffer as they arrive; ``close()``
      cancels the consumer (buffered, unacked messages return to the queue).
    """

    def __init__(
        self, queue: aio_pika.abc.AbstractQueue, *, prefetch: int = 50, type_prefix: str = ""
    ) -> None:
        self.queue = queue
        self.prefetch = prefetch
        self.type_prefix = type_prefix
        self._buffer: asyncio.Queue[aio_pika.abc.AbstractIncomingMessage] = asyncio.Queue()
        self._tag: str | None = None

    async def _start(self) -> None:
        if self._tag is None:
            await self.queue.channel.set_qos(prefetch_count=self.prefetch)
            self._tag = await self.queue.consume(self._buffer.put)

    def _decode(self, message: aio_pika.abc.AbstractIncomingMessage) -> ReceivedMessage:
        headers = message.headers or {}
        # RabbitMQ 4 quorum queues: x-acquired-count counts every earlier delivery
        # (including requeues); x-delivery-count only the failed ones (3.x semantics).
        previous = headers.get("x-acquired-count", headers.get("x-delivery-count"))
        if previous is not None:
            count: int | None = int(previous) + 1
        else:
            count = None if message.redelivered else 1
        return try_decode(
            lambda: decode_body(message.body, type_prefix=self.type_prefix),
            handle=message,
            delivery_count=count,
            message_id=message.message_id,
        )

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        await self._start()
        messages: list[aio_pika.abc.AbstractIncomingMessage] = []
        with contextlib.suppress(TimeoutError):
            messages.append(await asyncio.wait_for(self._buffer.get(), timeout=wait_seconds))
        while messages and len(messages) < max_messages and not self._buffer.empty():
            messages.append(self._buffer.get_nowait())
        return [self._decode(m) for m in messages]

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        for m in messages:
            await m.handle.ack()

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        for m in messages:
            await m.handle.nack(requeue=True)

    async def dead_letter(self, messages: Sequence[ReceivedMessage], reason: str) -> None:
        for m in messages:
            await m.handle.reject(requeue=False)

    async def close(self) -> None:
        if self._tag is not None:
            await self.queue.cancel(self._tag)
            self._tag = None
