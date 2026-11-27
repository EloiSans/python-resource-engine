"""What a repository and its unit of work can do, so the engine refuses
queries a store can't run (a sort without an index, a deep offset) with a
clear error instead of a slow scan or a store error.

Repositories and units of work declare their capabilities as class (or
instance) attributes; missing attributes take the defaults of a SQL store:

| Attribute | On | Default | Meaning |
|---|---|---|---|
| ``supports_conditions`` | repository | ``False`` | ``list`` applies row filters |
| ``supports_versions`` | repository | ``False`` | compare-and-set on ``expected_version`` |
| ``filterable`` | repository | ``None`` (any) | fields ``list`` can filter by |
| ``sortable`` | repository | ``None`` (any) | fields ``list`` can sort by |
| ``max_offset`` | repository | ``None`` (any) | deepest offset ``list`` pages to |
| ``transaction_scope`` | unit of work | ``"unit_of_work"`` | what commits atomically |
| ``max_writes`` | unit of work | ``None`` | writes one commit can hold |

``transaction_scope`` is ``"unit_of_work"`` (everything, like SQL),
``"partition"`` (atomic per partition key, Cosmos DB) or ``"none"`` (each
write on its own, remote APIs; ``transactional = False`` means the same).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from resource_engine.application.query import ListQuery
from resource_engine.errors import QueryNotSupported

type TransactionScope = Literal["unit_of_work", "partition", "none"]


@dataclass(frozen=True)
class Capabilities:
    conditions: bool = False
    versions: bool = False
    filterable: frozenset[str] | None = None
    sortable: frozenset[str] | None = None
    max_offset: int | None = None
    transaction_scope: TransactionScope = "unit_of_work"
    max_writes: int | None = None


def _fields(value: Iterable[str] | None) -> frozenset[str] | None:
    return None if value is None else frozenset(value)


def capabilities(repository: Any, uow: Any = None) -> Capabilities:
    """The declared capabilities of ``repository`` (and of its unit of work)."""
    scope = getattr(uow, "transaction_scope", None)
    if scope is None:
        scope = "none" if getattr(uow, "transactional", True) is False else "unit_of_work"
    return Capabilities(
        conditions=bool(getattr(repository, "supports_conditions", False)),
        versions=bool(getattr(repository, "supports_versions", False)),
        filterable=_fields(getattr(repository, "filterable", None)),
        sortable=_fields(getattr(repository, "sortable", None)),
        max_offset=getattr(repository, "max_offset", None),
        transaction_scope=scope,
        max_writes=getattr(uow, "max_writes", None),
    )


def check_query(resource: str, repository: Any, query: ListQuery) -> None:
    """Raise ``QueryNotSupported`` (RE-E103) when the repository can't run ``query``."""
    caps = capabilities(repository)
    problems: dict[str, Any] = {}
    if caps.filterable is not None:
        bad = sorted(f for f in query.filters if f not in caps.filterable)
        if bad:
            problems["filters"] = bad
    if caps.sortable is not None:
        bad = [o.lstrip("-") for o in query.order_by if o.lstrip("-") not in caps.sortable]
        if bad:
            problems["order_by"] = bad
    if caps.max_offset is not None and query.offset > caps.max_offset:
        problems["offset"] = query.offset
    if not problems:
        return
    parts = []
    if "filters" in problems:
        parts.append(f"can't filter by {problems['filters']}")
    if "order_by" in problems:
        parts.append(f"can't sort by {problems['order_by']}")
    if "offset" in problems:
        parts.append(f"can't page past offset {caps.max_offset}")
    raise QueryNotSupported(
        f"The store of {resource!r} " + "; ".join(parts),
        hint=(
            f"Filterable here: {sorted(caps.filterable) if caps.filterable is not None else 'any'}"
            f"; sortable: {sorted(caps.sortable) if caps.sortable is not None else 'any'}. "
            "Add an index to the store, or narrow the query."
        ),
        details={
            **problems,
            "filterable": sorted(caps.filterable) if caps.filterable is not None else None,
            "sortable": sorted(caps.sortable) if caps.sortable is not None else None,
        },
    )


def capability_problems(registry: Any, uow: Any) -> list[str]:
    """What the resources declare beyond what their repositories can do: call it
    at startup or in a test (``assert not capability_problems(registry, uow)``)
    with an open unit of work."""
    problems: list[str] = []
    for definition in registry:
        try:
            repository = uow.repository(definition.name)
        except Exception:  # noqa: BLE001 - served elsewhere (a composite's other store)
            continue
        caps = capabilities(repository, uow)
        if caps.filterable is not None:
            extra = sorted(set(definition.filterable) - caps.filterable)
            if extra:
                problems.append(f"{definition.name}: filterable {extra} not indexed by the store")
        if caps.sortable is not None:
            extra = sorted(set(definition.sortable) - caps.sortable)
            if extra:
                problems.append(f"{definition.name}: sortable {extra} not indexed by the store")
        if getattr(definition, "version_field", None) and not caps.versions:
            problems.append(f"{definition.name}: versioned, but the store can't compare versions")
    return problems


__all__ = [
    "Capabilities",
    "TransactionScope",
    "capabilities",
    "capability_problems",
    "check_query",
]
