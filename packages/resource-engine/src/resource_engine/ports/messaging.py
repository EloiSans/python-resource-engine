"""Messaging ports: outbound transport, transactional outbox and inbound handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from resource_engine.messaging.envelope import EventEnvelope


class MessagePublisher(Protocol):
    """Hands envelopes to a broker (SQS, SNS, Service Bus, Pub/Sub...).

    Must either accept every envelope or raise ``PublishFailed`` listing the ids
    that were not accepted. Delivery is at-least-once: consumers must tolerate
    duplicates (use ``EventEnvelope.id``).
    """

    async def publish(self, messages: Sequence[EventEnvelope]) -> None: ...


@dataclass(frozen=True)
class OutboxRecord:
    envelope: EventEnvelope
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None


class OutboxStore(Protocol):
    """Outbox table reached through the active unit of work."""

    async def add(self, envelopes: Sequence[EventEnvelope]) -> None: ...

    async def pending(self, limit: int) -> list[OutboxRecord]:
        """Oldest unpublished records. SQL implementations lock them (SKIP LOCKED)."""
        ...

    async def mark_published(self, ids: Sequence[str]) -> None: ...

    async def mark_failed(self, ids: Sequence[str], error: str) -> None: ...


@runtime_checkable
class SupportsOutbox(Protocol):
    """A unit of work that can write to an outbox in the same transaction."""

    def outbox(self) -> OutboxStore: ...


type MessageHandler = Callable[[EventEnvelope], Awaitable[None] | None]


# --- Inbound ---


@dataclass(frozen=True)
class ReceivedMessage:
    """A message taken from a broker (pull) or an HTTP push request.

    Attributes:
        envelope: The decoded envelope, or ``None`` when the body could not be decoded.
        handle: Broker token needed to ack/retry it (receipt handle, ack id, lock...).
        delivery_count: 1 on the first delivery; ``None`` if the broker does not say.
        error: Why decoding failed, when ``envelope`` is ``None``.
        message_id: Broker-side message id, for logs.
    """

    envelope: EventEnvelope | None
    handle: Any = None
    delivery_count: int | None = None
    error: str | None = None
    message_id: str | None = None


class MessageSource(Protocol):
    """Pull side of a broker queue or subscription."""

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        """Up to ``max_messages``, waiting at most ``wait_seconds`` when none are ready."""
        ...

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        """Remove the messages for good."""
        ...

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        """Make the messages available again, after ``delay_seconds`` if the broker can."""
        ...


@runtime_checkable
class SupportsDeadLetter(Protocol):
    """A source that can move messages to a dead-letter destination explicitly.

    Sources without it rely on the broker's own redrive policy (e.g. SQS
    ``maxReceiveCount``, Pub/Sub dead-letter topics).
    """

    async def dead_letter(self, messages: Sequence[ReceivedMessage], reason: str) -> None: ...


class InboxStore(Protocol):
    """Ids of messages a consumer has already processed (idempotent consumer)."""

    async def processed(self, consumer: str, message_ids: Sequence[str]) -> set[str]: ...

    async def mark_processed(self, consumer: str, message_ids: Sequence[str]) -> None: ...


@runtime_checkable
class SupportsInbox(Protocol):
    """A unit of work that exposes the inbox table."""

    def inbox(self) -> InboxStore: ...
