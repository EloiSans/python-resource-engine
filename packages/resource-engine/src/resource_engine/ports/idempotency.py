"""Idempotency port: results of writes, by caller and key, so a retried
request returns the first result instead of running again."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class IdempotencyRecord:
    """One completed write.

    Attributes:
        scope: Who sent it (``Principal.id``, ``system:<job>`` or ``anonymous``).
        key: The client's idempotency key.
        fingerprint: SHA-256 of the request (operation, resource, record, input).
        resource, operation: What ran.
        result: The result as JSON, to rebuild it on a replay.
        created_at, expires_at: When it ran and until when it is replayed.
    """

    scope: str
    key: str
    fingerprint: str
    resource: str
    operation: str
    result: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    expires_at: datetime | None = None


class IdempotencyStore(Protocol):
    """Records reached through the operation's unit of work.

    ``put`` happens in the operation's transaction: it commits with the change,
    or not at all. Two transactions putting the same ``(scope, key)`` must not
    both commit (a primary key in SQL).
    """

    async def get(self, scope: str, key: str) -> IdempotencyRecord | None: ...

    async def put(self, record: IdempotencyRecord) -> None:
        """Store ``record``, replacing an expired one with the same key."""
        ...

    async def purge(self, before: datetime, limit: int) -> int:
        """Delete up to ``limit`` records that expired before ``before``."""
        ...


@runtime_checkable
class SupportsIdempotency(Protocol):
    def idempotency(self) -> IdempotencyStore: ...
