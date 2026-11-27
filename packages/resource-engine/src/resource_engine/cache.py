"""Record caching: records read by id are kept in a ``Cache`` and forgotten
when the engine commits a change to them. See docs/caching.md.

```python
class CustomerResource(Resource):
    cache_ttl = 300                      # seconds; opt-in per resource

manager = ResourceManager(..., cache=InMemoryCache())   # or RedisCache(redis, secret=...)
```

The cache holds records as stored (entities), never responses: every read
still runs the caller's permission checks, row and field rules and tenancy on
the cached record. ``get`` (and the loads behind ``allowed_transitions`` and
``history``) read through the cache. Writes, transactions and lists always
go to the repository. A write through the manager forgets the record after
its commit. Changes made outside the manager (another service, SQL by hand)
show up when ``cache_ttl`` expires.
"""

from __future__ import annotations

import copy
import importlib
import time
from collections import OrderedDict
from typing import Any

from resource_engine.ports.cache import Cache

CLASS = "__class__"


class InMemoryCache:
    """A cache in this process: least recently used out after ``max_entries``.
    Each instance has its own, so other instances' writes reach it only when
    entries expire; use ``RedisCache`` with several instances."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    async def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(value)

    async def set(self, key: str, value: Any, ttl: float) -> None:
        self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._items.pop(key, None)

    async def add(self, key: str, value: Any, ttl: float) -> bool:
        if await self.get(key) is not None:
            return False
        await self.set(key, value, ttl)
        return True


def snapshot(entity: Any) -> dict[str, Any]:
    """An entity as plain data: its class and public attributes."""
    cls = type(entity)
    data = {k: v for k, v in vars(entity).items() if not k.startswith("_")}
    data[CLASS] = f"{cls.__module__}:{cls.__qualname__}"
    return data


def restore(data: dict[str, Any]) -> Any:
    """The entity a ``snapshot`` was taken from (a new, unattached instance)."""
    values = dict(data)
    module, _, qualname = values.pop(CLASS).partition(":")
    cls: Any = importlib.import_module(module)
    for part in qualname.split("."):
        cls = getattr(cls, part)
    try:
        return cls(**values)
    except TypeError:  # not every attribute is a constructor argument
        entity = cls.__new__(cls)
        for name, value in values.items():
            setattr(entity, name, value)
        return entity


class RecordCache:
    """Records by resource family and id, in a ``Cache``."""

    def __init__(self, cache: Cache, *, prefix: str = "re:rec:") -> None:
        self.cache = cache
        self.prefix = prefix

    def key(self, definition: Any, entity_id: Any) -> str:
        return f"{self.prefix}{definition.family}:{entity_id}"

    async def load(self, definition: Any, entity_id: Any) -> Any | None:
        try:
            data = await self.cache.get(self.key(definition, entity_id))
            entity = restore(data) if isinstance(data, dict) and CLASS in data else None
        except Exception:  # a cache problem is a miss, never a failed read
            import logging

            logging.getLogger("resource_engine.cache").warning(
                "Cache read failed for %s %s", definition.name, entity_id, exc_info=True
            )
            return None
        # a subtype reads only its own kind (a cached record of another is a miss)
        return entity if entity is not None and isinstance(entity, definition.model) else None

    async def store(self, definition: Any, entity: Any) -> None:
        try:
            await self.cache.set(
                self.key(definition, getattr(entity, definition.id_field)),
                snapshot(entity),
                float(definition.cache_ttl),
            )
        except Exception:
            import logging

            logging.getLogger("resource_engine.cache").warning(
                "Cache write failed for %s", definition.name, exc_info=True
            )

    async def forget(self, definition: Any, entity_id: Any) -> None:
        await self.cache.delete(self.key(definition, entity_id))


__all__ = ["InMemoryCache", "RecordCache", "restore", "snapshot"]
