from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from resource_engine.domain.events import DomainEvent


class EventPublisher(Protocol):
    """Publishes events after the transaction that produced them has committed.

    For guaranteed delivery implement this on top of an Outbox table written
    inside the transaction (planned; see docs/lifecycle.md).
    """

    async def publish(self, events: Sequence[DomainEvent]) -> None: ...


class NullEventPublisher:
    async def publish(self, events: Sequence[DomainEvent]) -> None:
        return None
