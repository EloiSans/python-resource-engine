"""Activity log port: what the worker did, for the explorer and operators.

The worker and the API are separate processes, so what one does and the
other shows goes through a store both reach: which consumers handled each
event and how (``record_delivery``, fed by ``ActivityMessageObserver``).
Adapters: ``InMemoryActivityLog`` (tests, one process) and
``SqlAlchemyActivityLog``. ``MessagingJanitor(activity_retention=)`` purges it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class DeliveryRecord:
    """One consumer's handling of one message.

    Attributes:
        outcome: ``ack``, ``duplicate``, ``retry``, ``dead_letter``... (the
            ``MessageProcessor`` outcome).
        error: Code and message of the failure, if any.
    """

    consumer: str
    message_id: str
    event_type: str
    outcome: str
    seconds: float
    error: str | None = None
    at: datetime = field(default_factory=_now)
    id: str = field(default_factory=_id)


class ActivityLog(Protocol):
    async def record_delivery(self, record: DeliveryRecord) -> None: ...

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        """Every consumer's handling of ``message_id``, oldest first."""
        ...

    async def purge(self, before: datetime, limit: int) -> int:
        """Delete up to ``limit`` records written before ``before``; returns how many."""
        ...


__all__ = ["ActivityLog", "DeliveryRecord"]
