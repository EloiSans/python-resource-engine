"""Retention for the audit trail. Run ``AuditJanitor.run_once()`` periodically."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from resource_engine.application.query import ListQuery
from resource_engine.audit.seal import AuditSealer, _before
from resource_engine.errors import ConfigurationError

logger = logging.getLogger("resource_engine.audit.janitor")


@dataclass
class AuditJanitorStats:
    sealed: int = 0  # entries sealed in this run
    seals: int = 0  # seals created
    purged: int = 0  # entries deleted
    purged_seals: int = 0  # seals whose entries were deleted


class AuditJanitor:
    """Seals pending entries (with a ``sealer``) and deletes old ones.

    Args:
        uow_factory: Units of work with the ``audit_log`` repository.
        retention: Age after which entries are deleted. ``None`` (the default)
            deletes nothing.
        sealer: With a sealer, each run seals first, and retention deletes
            whole sealed batches whose newest entry is past retention. The seal
            stays, marked ``purged_at``, so the chain still verifies. Unsealed
            entries are never deleted. Without one, entries older than
            ``retention`` are deleted directly.
        batch_size: Entries deleted per transaction.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        retention: timedelta | None = None,
        sealer: AuditSealer | None = None,
        batch_size: int = 1000,
        entries: str = "audit_log",
    ) -> None:
        if retention is not None and retention <= timedelta(0):
            raise ConfigurationError("retention must be positive or None")
        if batch_size < 1:
            raise ConfigurationError("batch_size must be at least 1")
        if retention is None and sealer is None:
            raise ConfigurationError(
                "AuditJanitor has nothing to do",
                hint="Pass retention=timedelta(days=...), a sealer, or both.",
                doc="docs/audit.md#retention",
            )
        self.uow_factory = uow_factory
        self.retention = retention
        self.sealer = sealer
        self.batch_size = batch_size
        self.entries_resource = entries

    async def run_once(self, now: datetime | None = None) -> AuditJanitorStats:
        now = now or datetime.now(UTC)
        stats = AuditJanitorStats()
        if self.sealer is not None:
            seals = await self.sealer.seal_pending(now)
            stats.seals = len(seals)
            stats.sealed = sum(s.entries for s in seals)
        if self.retention is not None:
            cutoff = now - self.retention
            if self.sealer is not None:
                await self._purge_sealed(cutoff, stats)
            else:
                stats.purged = await self._purge(_before(cutoff))
        if stats.seals or stats.purged:
            logger.info(
                "Audit: sealed %d entries in %d seals, purged %d entries",
                stats.sealed,
                stats.seals,
                stats.purged,
            )
        return stats

    async def _purge(self, condition: Any) -> int:
        total = 0
        while True:
            async with self.uow_factory() as uow:
                repo = uow.repository(self.entries_resource)
                page = await repo.list(ListQuery(limit=self.batch_size, condition=condition))
                for entry in page.items:
                    await repo.delete(entry)
                await uow.commit()
            total += len(page.items)
            if len(page.items) < self.batch_size:
                return total

    async def _purge_sealed(self, cutoff: datetime, stats: AuditJanitorStats) -> None:
        assert self.sealer is not None
        seals_resource = self.sealer.seals_resource
        while True:
            async with self.uow_factory() as uow:
                page = await uow.repository(seals_resource).list(
                    ListQuery(
                        limit=100,
                        condition={
                            "and": [
                                {"eq": [{"attr": "resource.purged_at"}, None]},
                                {"lt": [{"attr": "resource.last_at"}, cutoff]},
                            ]
                        },
                        order_by=["sequence"],
                    )
                )
                sequences = [seal.sequence for seal in page.items]
            if not sequences:
                return
            for sequence in sequences:
                stats.purged += await self._purge({"eq": [{"attr": "resource.seal"}, sequence]})
                async with self.uow_factory() as uow:
                    repo = uow.repository(seals_resource)
                    stored = await repo.get(sequence)
                    stored.purged_at = datetime.now(UTC)
                    await repo.update(stored)
                    await uow.commit()
                stats.purged_seals += 1

    async def run_forever(
        self, *, interval: float = 3600.0, stop: asyncio.Event | None = None
    ) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("Audit janitor run failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)


__all__ = ["AuditJanitor", "AuditJanitorStats"]
