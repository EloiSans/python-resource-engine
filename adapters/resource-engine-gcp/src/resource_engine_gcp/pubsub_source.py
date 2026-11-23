"""Pub/Sub subscription as a ``MessageSource`` (pull) and a decoder for push."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

from resource_engine.errors import UndecodableMessage
from resource_engine.messaging import EventEnvelope, decode_body, try_decode
from resource_engine.ports.messaging import ReceivedMessage

MAX_ACK_DEADLINE = 600


class PubSubMessageSource:
    """``MessageSource`` over a pull subscription.

    Args:
        client: ``google.cloud.pubsub_v1.SubscriberClient`` (honours
            ``PUBSUB_EMULATOR_HOST``). Its calls are blocking, so they run in a
            worker thread.
        subscription_path: ``projects/<project>/subscriptions/<name>``.
        type_prefix: CloudEvents type prefix to strip, if producers use one.

    ``retry`` sets the ack deadline to the delay (0 = redeliver now, max 600 s).
    Delivery counts are reported only when the subscription has a dead-letter
    policy, which is also what moves exhausted messages to the dead-letter topic.
    """

    def __init__(self, client: Any, subscription_path: str, *, type_prefix: str = "") -> None:
        self.client = client
        self.subscription = subscription_path
        self.type_prefix = type_prefix

    def _decode(self, received: Any) -> ReceivedMessage:
        return try_decode(
            lambda: decode_body(received.message.data, type_prefix=self.type_prefix),
            handle=received.ack_id,
            delivery_count=received.delivery_attempt or None,
            message_id=received.message.message_id,
        )

    async def _pull(self, max_messages: int, timeout: float) -> list[Any]:
        from google.api_core import exceptions

        try:
            response = await asyncio.to_thread(
                self.client.pull,
                request={"subscription": self.subscription, "max_messages": max_messages},
                timeout=max(timeout, 1.0),
            )
        except exceptions.DeadlineExceeded:
            return []
        return list(response.received_messages)

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            remaining = deadline - loop.time()
            received = await self._pull(max_messages, remaining)
            if received or remaining <= 0:
                return [self._decode(r) for r in received]
            await asyncio.sleep(min(0.2, max(remaining, 0)))

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        if messages:
            await asyncio.to_thread(
                self.client.acknowledge,
                request={
                    "subscription": self.subscription,
                    "ack_ids": [m.handle for m in messages],
                },
            )

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        if messages:
            await asyncio.to_thread(
                self.client.modify_ack_deadline,
                request={
                    "subscription": self.subscription,
                    "ack_ids": [m.handle for m in messages],
                    "ack_deadline_seconds": int(min(max(delay_seconds, 0), MAX_ACK_DEADLINE)),
                },
            )


def decode_pubsub_push(
    headers: Mapping[str, str], body: bytes, *, type_prefix: str = ""
) -> ReceivedMessage:
    """Decoder for ``build_event_receiver`` behind a Pub/Sub push subscription.

    Unwraps ``{"message": {"data": <base64>, "messageId": ...}, "deliveryAttempt": n}``.
    """

    def envelope() -> EventEnvelope:
        try:
            wrapper = json.loads(body)
            data = base64.b64decode(wrapper["message"]["data"], validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise UndecodableMessage(f"Not a Pub/Sub push request: {exc}") from exc
        return decode_body(data, type_prefix=type_prefix)

    try:
        wrapper = json.loads(body)
        message_id = wrapper["message"].get("messageId")
        attempt = wrapper.get("deliveryAttempt")
    except (ValueError, KeyError, TypeError, AttributeError):
        message_id, attempt = None, None
    return try_decode(envelope, delivery_count=attempt, message_id=message_id)
