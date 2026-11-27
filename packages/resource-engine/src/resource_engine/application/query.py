from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ListQuery:
    """Paging, equality filters and ordering for ``list`` operations.

    ``order_by`` items are field names; prefix with ``-`` for descending order.
    ``condition`` is a row filter in the policy condition language over
    ``resource.*`` (set by the engine for row-level permissions). Repositories that
    apply it declare ``supports_conditions = True``; the engine refuses to list
    through others when a condition is needed (RE-E705).
    """

    offset: int = 0
    limit: int = 50
    filters: Mapping[str, Any] = field(default_factory=dict)
    order_by: Sequence[str] = ()
    condition: Any = None
    #: Translatable fields of the resource, and the languages to compare and
    #: sort them in (set by the engine; see ``resource_engine.translatable``).
    translatable: frozenset[str] = frozenset()
    locales: Sequence[str] = ()


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int
    offset: int
    limit: int

    def map[U](self, fn: Callable[[T], U]) -> Page[U]:
        return Page([fn(item) for item in self.items], self.total, self.offset, self.limit)
