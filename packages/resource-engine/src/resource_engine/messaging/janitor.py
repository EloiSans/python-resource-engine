"""Retention for the messaging tables: inbox marks and published outbox records
grow with every message. Run ``MessagingJanitor.run_once()`` periodically (cron,
worker loop), or ``run_forever`` next to the relay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from resource_engine.errors import ConfigurationError
from resource_engine.ports.messaging import SupportsInbox, SupportsOutbox

logger = logging.getLogger("resource_engine.messaging.janitor")


@dataclass
class MessagingJanitorStats:
    inbox: int = 0
    outbox: int = 0
    activity: int = 0


class MessagingJanitor:
    """Deletes inbox marks and published outbox records past their retention.

    Args:
        uow_factory: Units of work with ``inbox()`` and/or ``outbox()``.
        inbox_retention: How long a consumer keeps the ids it processed. Must
            exceed the broker's longest redelivery window (message retention,
            dead-letter redrive): a message redelivered after its mark is gone
            runs again. ``None`` keeps the inbox untouched.
        outbox_retention: How long published records stay (for inspection and
            replays). Unpublished records are never deleted. ``None`` keeps them.
        activity / activity_retention: An ``ActivityLog`` and how long its
            records stay.
        batch_size: Rows deleted per transaction; ``run_once`` repeats until a
            batch comes back short.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        inbox_retention: timedelta | None = timedelta(days=14),
        outbox_retention: timedelta | None = timedelta(days=7),
        batch_size: int = 1000,
        activity: Any = None,
        activity_retention: timedelta = timedelta(days=7),
    ) -> None:
        if batch_size < 1:
            raise ConfigurationError("batch_size must be at least 1")
        for name, value in (("inbox", inbox_retention), ("outbox", outbox_retention)):
            if value is not None and value <= timedelta(0):
                raise ConfigurationError(f"{name}_retention must be positive or None")
        self.uow_factory = uow_factory
        self.inbox_retention = inbox_retention
        self.outbox_retention = outbox_retention
        self.batch_size = batch_size
        #: An ``ActivityLog`` purged after ``activity_retention`` (docs/explorer.md).
        self.activity = activity
        self.activity_retention = activity_retention

    async def run_once(self, now: datetime | None = None) -> MessagingJanitorStats:
        now = now or datetime.now(UTC)
        stats = MessagingJanitorStats()
        if self.inbox_retention is not None:
            before = now - self.inbox_retention
            stats.inbox = await self._drain(lambda uow: _inbox(uow).purge(before, self.batch_size))
        if self.outbox_retention is not None:
            before = now - self.outbox_retention
            stats.outbox = await self._drain(
                lambda uow: _outbox(uow).purge_published(before, self.batch_size)
            )
        if self.activity is not None:
            before = now - self.activity_retention
            while True:
                deleted = await self.activity.purge(before, self.batch_size)
                stats.activity += deleted
                if deleted < self.batch_size:
                    break
        if stats.inbox or stats.outbox:
            logger.info("Purged %d inbox marks, %d outbox records", stats.inbox, stats.outbox)
        return stats

    async def _drain(self, step: Callable[[Any], Any]) -> int:
        total = 0
        while True:
            async with self.uow_factory() as uow:
                deleted = await step(uow)
                await uow.commit()
            total += deleted
            if deleted < self.batch_size:
                return total

    async def run_forever(
        self, *, interval: float = 3600.0, stop: asyncio.Event | None = None
    ) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Messaging janitor run failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)


def _inbox(uow: Any) -> Any:
    if not isinstance(uow, SupportsInbox):
        raise ConfigurationError(
            f"{type(uow).__name__} has no inbox()",
            hint="Pass inbox_retention=None, or a unit of work with an inbox table.",
            doc="docs/messaging.md#retention",
        )
    return uow.inbox()


def _outbox(uow: Any) -> Any:
    if not isinstance(uow, SupportsOutbox):
        raise ConfigurationError(
            f"{type(uow).__name__} has no outbox()",
            hint="Pass outbox_retention=None, or a unit of work with an outbox table.",
            doc="docs/messaging.md#retention",
        )
    return uow.outbox()
