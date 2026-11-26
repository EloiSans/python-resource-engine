"""Base and subtype resources as joined tables: one base table with the common
columns and ids, one extension table per subtype with its own columns.

```python
requests = Table("requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(32), nullable=False, index=True),
    Column("title", String(200), nullable=False), ...)
vacation_requests = extension_table("vacation_requests", requests,
    Column("days", Integer, nullable=False))

map_family(mapper_registry, registry, "requests",
           {"requests": requests, "vacation_requests": vacation_requests, ...})
```

Reads of the base load every subtype's columns in the same query (one outer
join per subtype), so base records come back as their subtype's entity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.errors import ConfigurationError
from sqlalchemy import Column, ForeignKey, Table


def extension_table(name: str, base: Table, *columns: Any, id_column: str = "id") -> Table:
    """A subtype's table: its primary key is also a foreign key to the base row."""
    base_id = base.c[id_column]
    return Table(
        name,
        base.metadata,
        Column(id_column, base_id.type, ForeignKey(base_id), primary_key=True),
        *columns,
    )


def map_family(
    mapper_registry: Any, registry: ResourceRegistry, base: str, tables: Mapping[str, Table]
) -> None:
    """Map a base resource and its registered subtypes (joined-table inheritance)."""
    definition = registry.get(base)
    if definition.discriminator is None:
        raise ConfigurationError(f"{base} has no discriminator: it is not a base resource")
    subtypes = registry.subtypes(base)
    missing = [n for n in (base, *(d.name for d in subtypes.values())) if n not in tables]
    if missing:
        raise ConfigurationError(f"map_family({base!r}): no table for {missing}")
    base_table = tables[base]
    mapper_registry.map_imperatively(
        definition.model,
        base_table,
        polymorphic_on=base_table.c[definition.discriminator],
    )
    for identity, sub in subtypes.items():
        mapper_registry.map_imperatively(
            sub.model,
            tables[sub.name],
            inherits=definition.model,
            polymorphic_identity=identity,
            polymorphic_load="inline",  # base queries load the subtype columns too
        )


__all__ = ["extension_table", "map_family"]
