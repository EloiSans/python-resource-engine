"""View resources: read-only resources whose records are computed by a query.

```python
class CustomerBalanceResource(Resource):
    name = "customer_balances"
    model = CustomerBalance
    schemas = {"read": CustomerBalanceRead}
    operations = ["list", "get"]
    id_field = "customer_id"
    view = Aggregate(
        "invoices",
        group_by=["customer_id"],
        values={
            "invoices": Count(),
            "total": Sum("amount"),
            "pending": Sum("amount", where={"status": "pending"}),
            "last_issued": Max("issue_date"),
        },
    )
```

An ``Aggregate`` is neutral: every storage adapter computes it (SQL as one
``GROUP BY`` query, in memory in Python). ``view = True`` declares a custom
view that the storage adapter defines (a mapped ``select`` in SQLAlchemy, a
function for ``InMemoryStore``). Either way the resource lists and reads with
the usual filters, sorting, paging, permissions, tenancy, ``expand``, docs,
GraphQL and MCP tools, and never writes. See docs/views.md.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from resource_engine.errors import ConfigurationError

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry

DOC = "docs/views.md"


@dataclass(frozen=True)
class Measure:
    """One computed value of an aggregate row.

    Args:
        field: Source field (``None`` for ``Count``).
        where: Only source records matching these values count:
            ``{"status": "pending"}`` or ``{"status": ["pending", "approved"]}``.
    """

    field: str | None = None
    where: Mapping[str, Any] | None = None
    kind: str = dataclasses.field(default="", init=False)


@dataclass(frozen=True)
class Count(Measure):
    """Number of source records (in the group, matching ``where``)."""

    kind: str = dataclasses.field(default="count", init=False)


@dataclass(frozen=True)
class Sum(Measure):
    """Sum of ``field``; 0 when no record matches."""

    kind: str = dataclasses.field(default="sum", init=False)


@dataclass(frozen=True)
class Min(Measure):
    """Smallest ``field``; null when no record matches."""

    kind: str = dataclasses.field(default="min", init=False)


@dataclass(frozen=True)
class Max(Measure):
    """Largest ``field``; null when no record matches."""

    kind: str = dataclasses.field(default="max", init=False)


@dataclass(frozen=True)
class Avg(Measure):
    """Average of ``field``; null when no record matches."""

    kind: str = dataclasses.field(default="avg", init=False)


@dataclass(frozen=True)
class Aggregate:
    """Rows grouped from a source resource.

    Args:
        source: The resource whose records are grouped.
        group_by: Source fields of the group: one row per distinct value.
            Records without a value for the view's id field are left out.
        values: The computed fields, by name: ``Count()``, ``Sum("amount")``...
        where: Only source records matching these values are grouped.
    """

    source: str
    group_by: Sequence[str]
    values: Mapping[str, Measure]
    where: Mapping[str, Any] | None = None


def matches_where(record: Any, where: Mapping[str, Any] | None) -> bool:
    for name, expected in (where or {}).items():
        value = getattr(record, name, None)
        if isinstance(expected, list | tuple | set | frozenset):
            if value not in expected:
                return False
        elif value != expected:
            return False
    return True


def compute(view: Aggregate, records: Sequence[Any], key: str) -> list[dict[str, Any]]:
    """The aggregate rows of ``records`` (the in-memory implementation);
    ``key`` is the view's id field."""
    groups: dict[tuple[Any, ...], list[Any]] = {}
    for record in records:
        if not matches_where(record, view.where):
            continue
        if getattr(record, key, None) is None:
            continue
        group = tuple(getattr(record, f, None) for f in view.group_by)
        groups.setdefault(group, []).append(record)
    rows = []
    for group, members in groups.items():
        row = dict(zip(view.group_by, group, strict=True))
        for name, measure in view.values.items():
            row[name] = _measure(measure, members)
        rows.append(row)
    return rows


def _measure(measure: Measure, members: Sequence[Any]) -> Any:
    chosen = [m for m in members if matches_where(m, measure.where)]
    if measure.kind == "count":
        return len(chosen)
    values = [getattr(m, measure.field or "", None) for m in chosen]
    values = [v for v in values if v is not None]
    if measure.kind == "sum":
        return sum(values, start=type(values[0])(0)) if values else 0
    if not values:
        return None
    if measure.kind == "min":
        return min(values)
    if measure.kind == "max":
        return max(values)
    return sum(values) / len(values)


def check_view(owner: str, definition_fields: set[str], id_field: str, view: Any) -> None:
    """Shape of a view declaration (its source is checked with the registry)."""
    if view is True:
        return
    if not isinstance(view, Aggregate):
        raise ConfigurationError(
            f"{owner}: view must be an Aggregate(...) or True (a custom view)", doc=DOC
        )
    if not view.group_by:
        raise ConfigurationError(f"{owner}: the aggregate needs group_by fields", doc=DOC)
    for name, measure in view.values.items():
        if not isinstance(measure, Measure) or not measure.kind:
            raise ConfigurationError(
                f"{owner}: value {name!r} must be Count(), Sum(...), Min(...), Max(...) or "
                "Avg(...)",
                doc=DOC,
            )
        if measure.kind != "count" and not measure.field:
            raise ConfigurationError(f"{owner}: value {name!r} needs a source field", doc=DOC)
    produced = [*view.group_by, *view.values]
    if set(produced) != definition_fields:
        raise ConfigurationError(
            f"{owner}: the read schema fields {sorted(definition_fields)} differ from the "
            f"aggregate's {sorted(produced)}",
            hint="Declare one read field per group_by field and value.",
            doc=DOC,
        )
    if id_field not in view.group_by:
        raise ConfigurationError(
            f"{owner}: the id field {id_field!r} must be one of group_by {list(view.group_by)}",
            hint="Set id_field to the group's key (customer_id for totals per customer).",
            doc=DOC,
        )


def check_views(registry: ResourceRegistry) -> None:
    """Every aggregate's source and fields exist (run with the registry complete)."""
    for d in registry:
        view = d.view
        if not isinstance(view, Aggregate):
            continue
        if view.source not in registry:
            raise ConfigurationError(
                f"{d.name}: the view's source {view.source!r} is not registered", doc=DOC
            )
        source = registry.get(view.source)
        read = source.schemas.get("read")
        known = set(read.model_fields) if read is not None else set()
        used = {
            *view.group_by,
            *(view.where or {}),
            *(m.field for m in view.values.values() if m.field),
            *(f for m in view.values.values() for f in (m.where or {})),
        }
        unknown = sorted(used - known)
        if unknown:
            raise ConfigurationError(
                f"{d.name}: fields {unknown} are not in {view.source}'s read schema", doc=DOC
            )
        if d.tenant_field and d.tenant_field not in view.group_by:
            raise ConfigurationError(
                f"{d.name}: the tenant field {d.tenant_field!r} must be in group_by",
                hint="Each row then belongs to one tenant, like the records it sums up.",
                doc=DOC,
            )


__all__ = [
    "Aggregate",
    "Avg",
    "Count",
    "Max",
    "Measure",
    "Min",
    "Sum",
    "check_views",
    "compute",
]
