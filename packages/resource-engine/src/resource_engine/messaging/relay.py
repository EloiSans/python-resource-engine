from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from resource_engine.errors import PublishFailed
from resource_engine.ports.messaging import MessagePublisher, SupportsOutbox

logger = logging.getLogger("resource_engine.outbox")


class OutboxRelay:
    """Moves pending outbox records to a broker. At-least-once.

    Run ``run_once`` from a scheduled job, or ``run_forever`` in a worker process.
    Several relays may run concurrently on SQL stores that lock rows (SKIP LOCKED).
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        transport: MessagePublisher,
        *,
        batch_size: int = 100,
        max_attempts: int = 10,
    ) -> None:
        self.uow_factory = uow_factory
        self.transport = transport
        self.batch_size = batch_size
        self.max_attempts = max_attempts

    async def run_once(self) -> int:
        """Publish one batch. Returns how many messages were published."""
        async with self.uow_factory() as uow:
            if not isinstance(uow, SupportsOutbox):
                raise TypeError(f"{type(uow).__name__} has no outbox()")
            outbox = uow.outbox()
            records = [
                r for r in await outbox.pending(self.batch_size) if r.attempts < self.max_attempts
            ]
            if not records:
                return 0
            ids = [r.envelope.id for r in records]
            failed: list[str] = []
            try:
                await self.transport.publish([r.envelope for r in records])
            except PublishFailed as exc:
                failed = list(exc.failed_ids)
                await outbox.mark_failed(failed, exc.message)
                logger.warning("Outbox: %d of %d messages failed", len(failed), len(ids))
            except Exception as exc:
                await outbox.mark_failed(ids, f"{type(exc).__name__}: {exc}")
                await uow.commit()
                logger.exception("Outbox: publishing a batch of %d failed", len(ids))
                return 0
            published = [i for i in ids if i not in set(failed)]
            await outbox.mark_published(published)
            await uow.commit()
            return len(published)

    async def run_forever(
        self, *, interval: float = 1.0, stop: asyncio.Event | None = None
    ) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            published = await self.run_once()
            if published < self.batch_size:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=interval)
