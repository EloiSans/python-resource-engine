"""Decoding inbound message bodies into envelopes (transport-neutral).

Accepted formats, detected automatically:

- the envelope JSON written by the engine's publishers;
- CloudEvents 1.0 in structured mode (``application/cloudevents+json`` or any JSON
  object with ``specversion``);
- CloudEvents 1.0 in binary mode (``ce-*`` HTTP headers, JSON ``data`` as the body).

Broker adapters unwrap their own framing (SNS notifications, Pub/Sub push) first.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from resource_engine.errors import UndecodableMessage
from resource_engine.messaging.cloudevents import from_cloudevent
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import ReceivedMessage


def envelope_from_json(data: Any, *, type_prefix: str = "") -> EventEnvelope:
    """Envelope from a decoded JSON object (envelope or structured CloudEvent)."""
    if not isinstance(data, Mapping):
        raise UndecodableMessage(f"Expected a JSON object, got {type(data).__name__}")
    try:
        if "specversion" in data:
            return from_cloudevent(data, type_prefix=type_prefix)
        return EventEnvelope.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise UndecodableMessage(
            f"Not an event envelope or CloudEvent: {type(exc).__name__}: {exc}",
            hint="Producers must send the envelope JSON or a CloudEvents 1.0 event.",
        ) from exc


def decode_body(body: str | bytes, *, type_prefix: str = "") -> EventEnvelope:
    """Envelope from a JSON body. Raises ``UndecodableMessage`` (RE-E405)."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UndecodableMessage(f"Body is not JSON: {exc}") from exc
    return envelope_from_json(data, type_prefix=type_prefix)


def decode_http(headers: Mapping[str, str], body: bytes, *, type_prefix: str = "") -> EventEnvelope:
    """Envelope from an HTTP request (headers are matched case-insensitively)."""
    lower = {k.lower(): v for k, v in headers.items()}
    if "ce-id" in lower and "ce-specversion" in lower:  # CloudEvents binary mode
        event: dict[str, Any] = {k[3:]: v for k, v in lower.items() if k.startswith("ce-")}
        try:
            event["data"] = json.loads(body) if body else {}
        except ValueError as exc:
            raise UndecodableMessage(f"CloudEvent data is not JSON: {exc}") from exc
        return envelope_from_json(event, type_prefix=type_prefix)
    return decode_body(body, type_prefix=type_prefix)


def try_decode(
    decode: Callable[[], EventEnvelope],
    *,
    handle: Any = None,
    delivery_count: int | None = None,
    message_id: str | None = None,
) -> ReceivedMessage:
    """Run ``decode`` and wrap the result; decode errors become a poison message
    (``envelope=None``) that the processor dead-letters instead of crashing the loop.
    """
    try:
        envelope = decode()
    except UndecodableMessage as exc:
        return ReceivedMessage(None, handle, delivery_count, exc.message, message_id)
    return ReceivedMessage(envelope, handle, delivery_count, None, message_id or envelope.id)
