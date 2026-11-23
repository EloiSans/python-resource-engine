"""Clean-up job for documents, the storage counterpart of ``OutboxRelay``.

Rollback compensations and post-commit purges are best effort; a crash can skip
them. Run ``DocumentJanitor.run_once()`` periodically (cron, worker loop).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from resource_engine.application.query import ListQuery
from resource_engine.documents.manager import aware


@dataclass
class JanitorStats:
    expired: int = 0
    purged: int = 0
    orphans: int = 0


class DocumentJanitor:
    """Expires stale uploads, purges deleted content and removes orphan blobs.

    Args:
        documents: ``ResourceManager.documents``.
        grace: Blobs younger than this are never treated as orphans (an upload
            may be committing right now).
        batch: Documents handled per run and category.
    """

    def __init__(
        self, documents: Any, *, grace: timedelta = timedelta(hours=1), batch: int = 500
    ) -> None:
        self.documents = documents
        self.grace = grace
        self.batch = batch

    async def _with_status(self, status: str) -> list[Any]:
        async with self.documents.manager.uow_factory() as uow:
            page = await uow.repository(self.documents.resource).list(
                ListQuery(filters={"status": status}, limit=self.batch)
            )
            return [dataclasses.replace(d) for d in page.items]

    async def run_once(self, now: datetime | None = None) -> JanitorStats:
        now = now or datetime.now(UTC)
        stats = JanitorStats()
        manager = self.documents.manager
        machine = self.documents.machine

        expired = [d for d in await self._with_status("pending") if aware(d.expires_at) < now]
        if expired:
            async with manager.uow_factory() as uow:
                repo = uow.repository(self.documents.resource)
                for stale in expired:
                    doc = await repo.get(stale.id)
                    await machine.fire(doc, "discard", {})
                    doc.error = "upload expired"
                    await repo.update(doc)
                await uow.commit()
            stats.expired = len(expired)

        deleted = [d.id for d in await self._with_status("deleted") if d.purged_at is None]
        stats.purged = await self.documents.purge(deleted)

        cutoff = now - self.grace
        async with manager.uow_factory() as uow:
            repo = uow.repository(self.documents.resource)
            orphans = []
            async for info in self.documents.store.list(""):
                modified = aware(info.last_modified)
                if modified is None or modified > cutoff:
                    continue
                if await repo.get(info.key.rsplit("/", 1)[-1]) is None:
                    orphans.append(info.key)
                if len(orphans) >= self.batch:
                    break
        if orphans:
            await self.documents.store.delete(orphans)
        stats.orphans = len(orphans)
        return stats
