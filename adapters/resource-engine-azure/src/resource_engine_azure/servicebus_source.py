"""Service Bus queue or subscription as a ``MessageSource``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from resource_engine.messaging import decode_body, try_decode
from resource_engine.ports.messaging import ReceivedMessage


def _body(message: Any) -> bytes:
    body = message.body
    if isinstance(body, bytes | bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode()
    return b"".join(body)  # data body: iterable of byte sections


class ServiceBusMessageSource:
    """``MessageSource`` over an ``azure.servicebus.aio.ServiceBusReceiver``.

    Args:
        receiver: Receiver in ``PEEK_LOCK`` mode (the default), owned by the caller.
            For session-enabled entities, use a session receiver.
        type_prefix: CloudEvents type prefix to strip, if producers use one.

    ``retry`` abandons the message: Service Bus redelivers it at once (the delay
    is not supported) and counts the delivery. ``dead_letter`` moves it to the
    entity's dead-letter sub-queue with the reason. Service Bus also dead-letters
    by itself after ``MaxDeliveryCount``; keep that above the consumer's
    ``RetryPolicy.max_deliveries``.
    """

    def __init__(self, receiver: Any, *, type_prefix: str = "") -> None:
        self.receiver = receiver
        self.type_prefix = type_prefix

    def _decode(self, message: Any) -> ReceivedMessage:
        return try_decode(
            lambda: decode_body(_body(message), type_prefix=self.type_prefix),
            handle=message,
            delivery_count=message.delivery_count,
            message_id=message.message_id,
        )

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        messages = await self.receiver.receive_messages(
            max_message_count=max_messages, max_wait_time=wait_seconds or None
        )
        return [self._decode(m) for m in messages]

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        for m in messages:
            await self.receiver.complete_message(m.handle)

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        for m in messages:
            await self.receiver.abandon_message(m.handle)

    async def dead_letter(self, messages: Sequence[ReceivedMessage], reason: str) -> None:
        for m in messages:
            await self.receiver.dead_letter_message(
                m.handle, reason=reason[:200], error_description=reason[:1000]
            )
