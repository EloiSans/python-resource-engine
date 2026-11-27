"""Read-only resources over analytical query engines (Athena, BigQuery, Trino):
data lake tables served as view resources with the usual filters, sorting,
paging, row-level permissions and tenancy.

```python
from resource_engine.querying import QueryEngineStore
from resource_engine_http import TrinoEngine

store = QueryEngineStore(TrinoEngine(http, "http://trino:8080", catalog="lake"), registry,
                         tables={"sales": "lake.sales.daily"})
manager = ResourceManager(registry, store.unit_of_work)
```

- **Compiled queries:** ``list`` becomes one ``SELECT`` with ``WHERE``
  (filters, the row filter and the tenant, from the condition language),
  ``ORDER BY`` and the dialect's paging, plus one ``COUNT(*)``. Values are
  always parameters, never pasted into the SQL.
- **Read-only:** writes raise ``OperationNotEnabled``; declare the resource
  with ``operations = ["list", "get"]`` (a view).
- **Engines:** the ``QueryEngine`` port. ``TrinoEngine`` and
  ``BigQueryEngine`` (``resource-engine-http``), ``AthenaEngine``
  (``resource-engine-aws``). Engines with a SQLAlchemy dialect (Synapse
  serverless, Databricks SQL, Snowflake) are already served by
  ``resource-engine-sqlalchemy``'s ``map_view``.
- **Cost:** every engine adapter takes a limit (bytes scanned or billed) and a
  timeout; results can be cached with ``Resource.cache_ttl`` and the ``Cache``
  port like any record.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from resource_engine.application.query import ListQuery, Page
from resource_engine.errors import ConditionNotSupported, OperationNotEnabled

type Row = dict[str, Any]


class QueryEngine(Protocol):
    """Runs a read-only SQL query with positional ``?`` parameters."""

    dialect: Dialect

    async def query(self, sql: str, params: Sequence[Any]) -> list[Row]:
        """The rows, as dicts by column name. Raises ``RemoteCallFailed``
        (RE-E501) when the engine fails or a limit is hit."""
        ...


@dataclass(frozen=True)
class Dialect:
    """How an engine writes identifiers, paging and list membership."""

    name: str
    quote: str = '"'
    paging: Literal["offset_limit", "limit_offset"] = "offset_limit"
    array_contains: str = "contains({column}, {value})"

    def identifier(self, name: str) -> str:
        if not all(part.replace("_", "").isalnum() for part in name.split(".")):
            raise ConditionNotSupported(f"Unsafe identifier {name!r}")
        q = self.quote
        return ".".join(f"{q}{part}{q}" for part in name.split("."))

    def page(self, offset: int, limit: int) -> str:
        if self.paging == "limit_offset":
            return f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return f" OFFSET {int(offset)} LIMIT {int(limit)}"


#: Trino, Presto and Athena (engine v3).
TRINO = Dialect("trino")
#: Google BigQuery (GoogleSQL).
BIGQUERY = Dialect(
    "bigquery", quote="`", paging="limit_offset", array_contains="{value} IN UNNEST({column})"
)

_COMPARISONS = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}


class Compiler:
    """The condition language and ``ListQuery`` as SQL with ``?`` parameters."""

    def __init__(self, dialect: Dialect) -> None:
        self.dialect = dialect
        self.params: list[Any] = []

    def _value(self, value: Any) -> str:
        self.params.append(value)
        return "?"

    def _operand(self, node: Any) -> tuple[str, bool]:
        """SQL of an operand, and whether it is a column."""
        if isinstance(node, Mapping) and "attr" in node:
            path = str(node["attr"])
            if not path.startswith("resource."):
                raise ConditionNotSupported(f"Only record attributes can be queried, not {path!r}")
            return self.dialect.identifier(path.removeprefix("resource.")), True
        return self._value(node), False

    def condition(self, node: Any) -> str:
        if node is True:
            return "1 = 1"
        if node is False:
            return "1 = 0"
        if not isinstance(node, Mapping) or len(node) != 1:
            raise ConditionNotSupported(f"Unsupported condition {node!r}")
        op, args = next(iter(node.items()))
        if op in ("and", "or"):
            parts = [self.condition(a) for a in args]
            if not parts:
                return "1 = 1" if op == "and" else "1 = 0"
            return "(" + f" {op.upper()} ".join(parts) + ")"
        if op == "not":
            return f"(NOT {self.condition(args)})"
        if op == "exists":
            column, _ = self._operand(args)
            return f"{column} IS NOT NULL"
        if op in _COMPARISONS:
            left, right = args
            if right is None or left is None:
                column, _ = self._operand(left if right is None else right)
                return f"{column} IS {'NOT ' if op == 'ne' else ''}NULL"
            return f"{self._operand(left)[0]} {_COMPARISONS[op]} {self._operand(right)[0]}"
        if op in ("in", "nin"):
            left, values = args
            column, _ = self._operand(left)
            if not values:
                return "1 = 0" if op == "in" else "1 = 1"
            listed = ", ".join(self._value(v) for v in values)
            return f"{column} {'NOT IN' if op == 'nin' else 'IN'} ({listed})"
        if op == "contains":
            collection, item = args
            column, _ = self._operand(collection)
            return self.dialect.array_contains.format(column=column, value=self._value(item))
        raise ConditionNotSupported(f"Unsupported operator {op!r} for a query engine")

    def where(self, query: ListQuery) -> str:
        parts = []
        for name, value in query.filters.items():
            if "." in name:
                raise ConditionNotSupported(
                    f"Related filters ({name}) are not available on query-engine resources"
                )
            parts.append(self.condition({"eq": [{"attr": f"resource.{name}"}, value]}))
        if query.condition is not None:
            parts.append(self.condition(query.condition))
        return f" WHERE {' AND '.join(parts)}" if parts else ""

    def order(self, query: ListQuery, default: str | None) -> str:
        items = list(query.order_by) or ([default] if default else [])
        if not items:
            return ""
        rendered = [
            f"{self.dialect.identifier(o.lstrip('-'))} {'DESC' if o.startswith('-') else 'ASC'}"
            for o in items
        ]
        return " ORDER BY " + ", ".join(rendered)


def _columns(model: type) -> list[str]:
    if dataclasses.is_dataclass(model):
        return [f.name for f in dataclasses.fields(model)]
    if hasattr(model, "model_fields"):
        return list(model.model_fields)  # type: ignore[attr-defined]
    return list(typing.get_type_hints(model))


class QueryEngineRepository:
    """A read-only ``Repository`` over one table of a ``QueryEngine``."""

    supports_conditions = True
    supports_versions = False

    def __init__(
        self, engine: QueryEngine, table: str, model: type, *, id_field: str = "id"
    ) -> None:
        self.engine = engine
        self.table = table
        self.model = model
        self.id_field = id_field
        self.columns = _columns(model)

    def _select(self, compiler: Compiler) -> str:
        names = ", ".join(compiler.dialect.identifier(c) for c in self.columns)
        return f"SELECT {names} FROM {compiler.dialect.identifier(self.table)}"

    def _entity(self, row: Row) -> Any:
        return self.model(**{c: row.get(c) for c in self.columns})

    async def get(self, entity_id: Any) -> Any:
        compiler = Compiler(self.engine.dialect)
        where = compiler.condition({"eq": [{"attr": f"resource.{self.id_field}"}, entity_id]})
        rows = await self.engine.query(
            f"{self._select(compiler)} WHERE {where}{compiler.dialect.page(0, 1)}",
            compiler.params,
        )
        return self._entity(rows[0]) if rows else None

    async def list(self, query: ListQuery) -> Page[Any]:
        counter = Compiler(self.engine.dialect)
        where = counter.where(query)
        total_rows = await self.engine.query(
            f"SELECT COUNT(*) AS total FROM {counter.dialect.identifier(self.table)}{where}",
            counter.params,
        )
        total = int(next(iter(total_rows[0].values()))) if total_rows else 0
        compiler = Compiler(self.engine.dialect)
        sql = (
            self._select(compiler)
            + compiler.where(query)
            + compiler.order(query, self.id_field if self.id_field in self.columns else None)
            + compiler.dialect.page(query.offset, query.limit)
        )
        rows = await self.engine.query(sql, compiler.params)
        return Page([self._entity(r) for r in rows], total, query.offset, query.limit)

    async def _read_only(self, *args: Any, **kw: Any) -> Any:
        raise OperationNotEnabled(
            f"{self.table} is read-only: it is served by a query engine",
            hint="Declare the resource with operations = ['list', 'get'].",
        )

    add = update = delete = _read_only


class QueryEngineUnitOfWork:
    """Hands out the query-engine repositories; there is nothing to commit."""

    def __init__(self, repositories: Mapping[str, QueryEngineRepository]) -> None:
        self.repositories = repositories

    def repository(self, resource_name: str) -> QueryEngineRepository:
        return self.repositories[resource_name]

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> QueryEngineUnitOfWork:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class QueryEngineStore:
    """Read-only resources served by a query engine.

    Args:
        engine: The ``QueryEngine``.
        registry: The registry; its resources named in ``tables`` are served.
        tables: Resource name → table (``catalog.schema.table`` for Trino,
            ``dataset.table`` for BigQuery, ``database.table`` for Athena).
    """

    def __init__(self, engine: QueryEngine, registry: Any, tables: Mapping[str, str]) -> None:
        self.engine = engine
        self.repositories = {}
        for name, table in tables.items():
            definition = registry.get(name)
            self.repositories[name] = QueryEngineRepository(
                engine, table, definition.model, id_field=definition.id_field
            )

    def unit_of_work(self) -> QueryEngineUnitOfWork:
        return QueryEngineUnitOfWork(self.repositories)


__all__ = [
    "BIGQUERY",
    "TRINO",
    "Compiler",
    "Dialect",
    "QueryEngine",
    "QueryEngineRepository",
    "QueryEngineStore",
    "QueryEngineUnitOfWork",
]
