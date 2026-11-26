"""Cache port: values by key with a time to live. ``resource_engine.cache``
ships ``InMemoryCache``; ``resource-engine-redis`` a shared ``RedisCache``.

Values are plain data (dicts, lists, strings, numbers, dates, decimals);
implementations serialise them as they need.
"""

from __future__ import annotations

from typing import Any, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Any | None:
        """The value, or ``None`` when missing or expired."""
        ...

    async def set(self, key: str, value: Any, ttl: float) -> None:
        """Store ``value`` for ``ttl`` seconds."""
        ...

    async def delete(self, *keys: str) -> None:
        """Forget ``keys`` (missing ones are ignored)."""
        ...
