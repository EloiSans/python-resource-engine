"""Audit sinks: the database (default), logs, or several at once."""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from resource_engine.audit.model import AuditEntry
from resource_engine.audit.recorder import jsonable

log = logging.getLogger("resource_engine.audit")


class RepositoryAuditSink:
    """Writes entries to the built-in ``audit_log`` resource (any repository: SQL
    table, in memory). Changes go into the operation's own transaction.

    Args:
        uow_factory: Opens a unit of work for entries written on their own.
        resource: Name of the audit resource (``registry.register_audit()``).
    """

    def __init__(self, uow_factory: Callable[[], Any], *, resource: str = "audit_log") -> None:
        self.uow_factory = uow_factory
        self.resource = resource

    async def record(self, entries: Sequence[AuditEntry], *, uow: Any = None) -> None:
        if not entries:
            return
        if uow is not None:
            repo = uow.repository(self.resource)
            for entry in entries:
                await repo.add(entry)
            return
        async with self.uow_factory() as own:
            repo = own.repository(self.resource)
            for entry in entries:
                await repo.add(entry)
            await own.commit()


class LoggingAuditSink:
    """One structured log line (JSON) per entry, for deployments that ship logs."""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        self.logger = logger or log
        self.level = level

    async def record(self, entries: Sequence[AuditEntry], *, uow: Any = None) -> None:
        for entry in entries:
            self.logger.log(self.level, "audit %s", json.dumps(jsonable(dataclasses.asdict(entry))))


class CompositeAuditSink:
    """Sends entries to every sink, in order."""

    def __init__(self, *sinks: Any) -> None:
        self.sinks = sinks

    async def record(self, entries: Sequence[AuditEntry], *, uow: Any = None) -> None:
        for sink in self.sinks:
            await sink.record(entries, uow=uow)
