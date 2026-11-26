"""An in-process event hub: committed events fanned out to live subscribers
(GraphQL subscriptions, server-sent events, WebSockets).

Feed it like any broker (it is a ``MessagePublisher``):

- **directly after commit:** ``ResourceManager(event_publisher=TransportEventPublisher(hub))``;
- **from the outbox:** ``OutboxRelay(uow_factory, FanoutPublisher(broker, hub))``,
  or ``ProcessFeed(..., then=hub)``;
- **with several instances:** each instance consumes a broadcast topic with
  ``MessageConsumer(source, MessageProcessor(hub.router(), name=...))``, so
  every instance's subscribers see every event.

Subscribers get envelopes as an async iterator. A slow subscriber's queue is
bounded: when it is full the oldest event is dropped and the subscriber is told
(``overflowed``), so one stuck client never holds the others back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import EventEnvelope

logger = logging.getLogger("resource_engine.hub")

type EventFilter = Callable[[EventEnvelope], bool]


class Subscription:
    """One subscriber's queue of envelopes (``async for envelope in sub``)."""

    def __init__(self, hub: EventHub, matches: EventFilter | None, size: int) -> None:
        self.hub = hub
        self.matches = matches
        self.queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(maxsize=size)
        self.overflowed = 0
        self.closed = False

    def offer(self, envelope: EventEnvelope) -> None:
        if self.closed or (self.matches is not None and not self.matches(envelope)):
            return
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest
            self.overflowed += 1
        self.queue.put_nowait(envelope)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.hub._subscriptions.discard(self)
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(None)

    def __aiter__(self) -> AsyncIterator[EventEnvelope]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[EventEnvelope]:
        try:
            while True:
                envelope = await self.queue.get()
                if envelope is None:
                    return
                yield envelope
        finally:
            self.close()


class EventHub:
    """Fans envelopes out to in-process subscribers.

    Args:
        queue_size: Events kept per subscriber before the oldest are dropped.
    """

    def __init__(self, *, queue_size: int = 100) -> None:
        self.queue_size = queue_size
        self._subscriptions: set[Subscription] = set()

    @property
    def subscribers(self) -> int:
        return len(self._subscriptions)

    def subscribe(self, matches: EventFilter | None = None) -> Subscription:
        """A new subscription to the envelopes ``matches`` accepts (all by default)."""
        subscription = Subscription(self, matches, self.queue_size)
        self._subscriptions.add(subscription)
        return subscription

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        for envelope in messages:
            for subscription in list(self._subscriptions):
                try:
                    subscription.offer(envelope)
                except Exception:  # a broken filter must not stop the others
                    logger.exception("Hub subscriber filter failed")

    def router(self, name: str = "hub") -> EventRouter:
        """An ``EventRouter`` that hands every event to the hub (for consumers)."""
        hub = self

        class _Everything(EventRouter):
            async def dispatch(self, envelope: EventEnvelope) -> None:
                await hub.publish([envelope])

        return _Everything(name=name, description="Live subscribers (GraphQL, SSE)")

    def close(self) -> None:
        for subscription in list(self._subscriptions):
            subscription.close()


class FanoutPublisher:
    """Publishes to several publishers (a broker and the hub). A failure in one
    is reported with the ids it rejected; the others still get the messages."""

    def __init__(self, *publishers: Any) -> None:
        self.publishers = publishers

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        failed: list[str] = []
        errors: list[str] = []
        for publisher in self.publishers:
            try:
                await publisher.publish(messages)
            except PublishFailed as exc:
                failed += [i for i in exc.failed_ids if i not in failed]
                errors.append(exc.message)
            except Exception as exc:
                failed += [m.id for m in messages if m.id not in failed]
                errors.append(f"{type(exc).__name__}: {exc}")
        if failed:
            raise PublishFailed("; ".join(errors), failed_ids=failed)


__all__ = ["EventHub", "FanoutPublisher", "Subscription"]
