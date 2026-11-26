"""View resources in SQL: the view's model is mapped onto a ``SELECT``, so the
generic repository lists and reads it with filters, row conditions, sorting
and paging. Nothing is created in the database (no migration).

```python
map_entities()                         # the sources first
map_views(mapper_registry, registry)   # every Aggregate view: one GROUP BY query

# a custom view (view = True on the resource): any select, with its key
map_view(mapper_registry, InvoiceBalance, select(...).subquery("invoice_balances"),
         primary_key="id")
```
"""

from __future__ import annotations

from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.application.views import Aggregate, Measure
from resource_engine.errors import ConfigurationError
from sqlalchemy import and_, case, cast, func, select
from sqlalchemy import inspect as sa_inspect


def map_view(mapper_registry: Any, model: type, selectable: Any, *, primary_key: str) -> None:
    """Map a view's model onto ``selectable`` (a subquery), keyed by ``primary_key``."""
    if not hasattr(selectable, "c"):
        selectable = selectable.subquery()
    mapper_registry.map_imperatively(model, selectable, primary_key=[selectable.c[primary_key]])


def map_views(mapper_registry: Any, registry: ResourceRegistry) -> None:
    """Map every registered ``Aggregate`` view (its source must be mapped)."""
    for d in registry:
        if isinstance(d.view, Aggregate) and not _mapped(d.model):
            map_view(
                mapper_registry,
                d.model,
                aggregate_select(registry, d.view, d.id_field).subquery(d.name),
                primary_key=d.id_field,
            )


def aggregate_select(registry: ResourceRegistry, view: Aggregate, key: str) -> Any:
    """The ``GROUP BY`` query of an aggregate view keyed by ``key``."""
    source = registry.get(view.source).model
    if not _mapped(source):
        raise ConfigurationError(
            f"Map {source.__name__} before the views over {view.source}",
            doc="docs/views.md",
        )
    keys = [getattr(source, f) for f in view.group_by]
    values = [_measure(source, m).label(name) for name, m in view.values.items()]
    return (
        select(*(k.label(f) for k, f in zip(keys, view.group_by, strict=True)), *values)
        .where(getattr(source, key).is_not(None), *_where(source, view.where))
        .group_by(*keys)
    )


def _where(source: Any, where: Any) -> list[Any]:
    out = []
    for name, expected in (where or {}).items():
        column = getattr(source, name)
        many = isinstance(expected, list | tuple | set | frozenset)
        out.append(column.in_(list(expected)) if many else column == expected)
    return out


def _measure(source: Any, measure: Measure) -> Any:
    condition = _where(source, measure.where)
    if measure.kind == "count":
        return func.count(case((and_(*condition), 1))) if condition else func.count()
    column = getattr(source, measure.field or "")
    value = case((and_(*condition), column)) if condition else column
    if measure.kind == "sum":
        # same type (and scale) as the column, also when nothing matches
        return cast(func.coalesce(func.sum(value), 0), column.type)
    return {"min": func.min, "max": func.max, "avg": func.avg}[measure.kind](value)


def _mapped(model: type) -> bool:
    return sa_inspect(model, raiseerr=False) is not None


__all__ = ["aggregate_select", "map_view", "map_views"]
