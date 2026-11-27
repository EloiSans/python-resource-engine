"""Retention for the audit trail. Run ``AuditJanitor.run_once()`` periodically."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from resource_engine.application.query import ListQuery
from resource_engine.audit.seal import AuditSealer, _before
from resource_engine.errors import ConfigurationError

logger = logging.getLogger("resource_engine.audit.janitor")


@dataclass(frozen=True)
class Retention:
    """How long to keep the entries that match: a resource (or several), an
    outcome (``ok``, ``denied``, ``failed``), or both. ``keep=None`` keeps them
    forever. The first rule that matches an entry decides."""

    keep: timedelta | None
    resource: str | Sequence[str] | None = None
    outcome: str | Sequence[str] | None = None

    def __post_init__(self) -> None:
        if self.keep is not None and self.keep <= timedelta(0):
            raise ConfigurationError("Retention.keep must be positive or None")
        if self.resource is None and self.outcome is None:
            raise ConfigurationError(
                "A retention rule needs a resource or an outcome",
                hint="For everything else, use AuditJanitor(retention=...).",
            )

    @staticmethod
    def _values(value: str | Sequence[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [value] if isinstance(value, str) else list(value)

    def condition(self) -> dict[str, Any]:
        parts = []
        for field, value in (("resource", self.resource), ("outcome", self.outcome)):
            values = self._values(value)
            if values is not None:
                parts.append({"in": [{"attr": f"resource.{field}"}, values]})
        return parts[0] if len(parts) == 1 else {"and": parts}

    def matches(self, entry: Any) -> bool:
        resources, outcomes = self._values(self.resource), self._values(self.outcome)
        return (resources is None or entry.resource in resources) and (
            outcomes is None or entry.outcome in outcomes
        )


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
        rules: ``Retention`` rules per resource or outcome, before the
            default: ``Retention(timedelta(days=30), outcome="denied")``,
            ``Retention(None, resource="invoices")`` (kept forever). The first
            that matches an entry decides; entries no rule matches follow
            ``retention``.
        sealer: With a sealer, each run seals first, and retention deletes
            whole sealed batches once every entry in them is past its own
            retention. The seal stays, marked ``purged_at``, so the chain still
            verifies. Unsealed entries are never deleted. Without one, entries older than
            ``retention`` are deleted directly.
        batch_size: Entries deleted per transaction.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        retention: timedelta | None = None,
        rules: Sequence[Retention] = (),
        sealer: AuditSealer | None = None,
        batch_size: int = 1000,
        entries: str = "audit_log",
    ) -> None:
        if retention is not None and retention <= timedelta(0):
            raise ConfigurationError("retention must be positive or None")
        if batch_size < 1:
            raise ConfigurationError("batch_size must be at least 1")
        if retention is None and sealer is None and not any(r.keep for r in rules):
            raise ConfigurationError(
                "AuditJanitor has nothing to do",
                hint="Pass retention=timedelta(days=...), a sealer, or both.",
                doc="docs/audit.md#retention",
            )
        self.uow_factory = uow_factory
        self.retention = retention
        self.rules = list(rules)
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
        keeps = [r.keep for r in self.rules if r.keep] + (
            [self.retention] if self.retention else []
        )
        if keeps:
            if self.sealer is not None:
                await self._purge_sealed(now, min(keeps), stats)
            else:
                for condition in self._conditions(now):
                    stats.purged += await self._purge(condition)
        if stats.seals or stats.purged:
            logger.info(
                "Audit: sealed %d entries in %d seals, purged %d entries",
                stats.sealed,
                stats.seals,
                stats.purged,
            )
        return stats

    def _conditions(self, now: datetime) -> list[dict[str, Any]]:
        """One delete condition per rule (and the default): its entries, past its
        retention, and not taken by an earlier rule."""
        out: list[dict[str, Any]] = []
        earlier: list[dict[str, Any]] = []
        for rule in self.rules:
            if rule.keep is not None:
                parts = [rule.condition(), _before(now - rule.keep)]
                if earlier:
                    parts.append({"not": {"or": list(earlier)}})
                out.append({"and": parts})
            earlier.append(rule.condition())
        if self.retention is not None:
            parts = [_before(now - self.retention)]
            if earlier:
                parts.append({"not": {"or": list(earlier)}})
            out.append(parts[0] if len(parts) == 1 else {"and": parts})
        return out

    def keep_for(self, entry: Any) -> timedelta | None:
        """How long ``entry`` is kept (``None``: forever)."""
        for rule in self.rules:
            if rule.matches(entry):
                return rule.keep
        return self.retention

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

    async def _purge_sealed(
        self, now: datetime, shortest: timedelta, stats: AuditJanitorStats
    ) -> None:
        """Delete sealed batches once every entry is past its own retention."""
        assert self.sealer is not None
        seals_resource = self.sealer.seals_resource
        after = 0
        while True:
            async with self.uow_factory() as uow:
                page = await uow.repository(seals_resource).list(
                    ListQuery(
                        limit=100,
                        condition={
                            "and": [
                                {"eq": [{"attr": "resource.purged_at"}, None]},
                                {"lt": [{"attr": "resource.last_at"}, now - shortest]},
                                {"gt": [{"attr": "resource.sequence"}, after]},
                            ]
                        },
                        order_by=["sequence"],
                    )
                )
                sequences = [seal.sequence for seal in page.items]
            if not sequences:
                return
            for sequence in sequences:
                after = sequence
                if not await self._expired(sequence, now):
                    continue
                stats.purged += await self._purge({"eq": [{"attr": "resource.seal"}, sequence]})
                async with self.uow_factory() as uow:
                    repo = uow.repository(seals_resource)
                    stored = await repo.get(sequence)
                    stored.purged_at = datetime.now(UTC)
                    await repo.update(stored)
                    await uow.commit()
                stats.purged_seals += 1

    async def _expired(self, sequence: int, now: datetime) -> bool:
        """Whether every entry of a sealed batch is past its retention."""
        offset = 0
        while True:
            async with self.uow_factory() as uow:  # read inside: SQL entries detach after
                page = await uow.repository(self.entries_resource).list(
                    ListQuery(
                        limit=self.batch_size,
                        offset=offset,
                        condition={"eq": [{"attr": "resource.seal"}, sequence]},
                    )
                )
                for entry in page.items:
                    keep = self.keep_for(entry)
                    at = entry.at if entry.at.tzinfo else entry.at.replace(tzinfo=UTC)
                    if keep is None or at > now - keep:
                        return False
            if len(page.items) < self.batch_size:
                return True
            offset += self.batch_size

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


__all__ = ["AuditJanitor", "AuditJanitorStats", "Retention"]
