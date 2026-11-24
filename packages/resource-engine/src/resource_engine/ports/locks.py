"""Lock port: named leases so a job runs on one worker at a time."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol


class LockProvider(Protocol):
    """Named leases with an expiry.

    A lease is held by ``owner`` until it is released or ``ttl`` passes, so a
    worker that dies frees it after ``ttl``. Acquiring a lease the same owner
    already holds renews it.
    """

    async def acquire(self, name: str, owner: str, ttl: timedelta) -> bool:
        """Take or renew the lease. ``False`` when another owner holds it."""
        ...

    async def release(self, name: str, owner: str) -> None:
        """Give the lease up. Does nothing when ``owner`` does not hold it."""
        ...
