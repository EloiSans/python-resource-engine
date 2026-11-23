"""Audit port: where audit entries go."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from resource_engine.audit.model import AuditEntry
    from resource_engine.ports.unit_of_work import UnitOfWork


class AuditSink(Protocol):
    async def record(self, entries: Sequence[AuditEntry], *, uow: UnitOfWork | None = None) -> None:
        """Store ``entries``.

        With ``uow`` (committed changes) the entries belong to that transaction:
        they are committed or rolled back with the change. Without it (refused
        attempts, audited reads) the sink writes on its own.
        """
        ...
