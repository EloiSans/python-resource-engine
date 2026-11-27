"""Azure Cosmos DB (NoSQL API) repositories (extra ``[cosmos]``).

```python
from azure.cosmos.aio import CosmosClient
from resource_engine_azure import CosmosContainer, CosmosStore

client = CosmosClient(URL, credential=DefaultAzureCredential())
store = CosmosStore(client.get_database_client("invoices"), registry, {
    "partners": "partners",                                       # partition key /id
    "orders": CosmosContainer("orders", partition_key="partner_id"),
})
manager = ResourceManager(registry, store.unit_of_work)
```

- **Writes** go in transactional batches at commit, one per partition key
  (up to 100 operations each): atomic within a partition, not across
  partitions (``transaction_scope = "partition"``). Partition by the field
  that groups records changed together (an order's lines by the order).
- **Versions:** versioned writes read the stored item first and send its
  ETag with the batch, so a concurrent change fails the batch and the
  operation gets ``VersionConflict``.
- **Lists** are SQL queries: filters and row filters in ``WHERE`` with
  parameters, ``ORDER BY``, ``OFFSET``/``LIMIT`` and a ``COUNT`` for the
  total, across partitions. Sorting on several fields needs a composite
  index; name the sortable fields you indexed with ``sortable=``.
- **Ids:** Cosmos ids are strings; numeric ids are stored as text and read
  back as numbers.
- **Types:** ``Decimal`` values are stored as JSON numbers (doubles; exact
  amounts: use integer cents); dates and timestamps as ISO 8601 strings (UTC).
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from resource_engine.application.query import ListQuery
from resource_engine.errors import ConditionNotSupported, RemoteCallFailed
from resource_engine.nosql import (
    DocumentCodec,
    DocumentRepository,
    DocumentUnitOfWork,
    Write,
    version_conflict,
)
from resource_engine.querying import Compiler, Dialect

# ------------------------------------------------------------------ values


def to_json(value: Any) -> Any:
    """A Python value as JSON for Cosmos DB."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        return int(value) if isinstance(exponent, int) and exponent >= 0 else float(value)
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value
        return moment.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return to_json(value.value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    return str(value)


# ------------------------------------------------------------- the queries


@dataclass(frozen=True)
class _CosmosDialect(Dialect):
    def identifier(self, name: str) -> str:
        if not all(part.replace("_", "").isalnum() for part in name.split(".")):
            raise ConditionNotSupported(f"Unsafe identifier {name!r}")
        return "c" + "".join(f'["{part}"]' for part in name.split("."))


COSMOS = _CosmosDialect("cosmos", array_contains="ARRAY_CONTAINS({column}, {value})")


class CosmosCompiler(Compiler):
    """The condition language as Cosmos SQL with ``@pN`` parameters."""

    def __init__(self) -> None:
        super().__init__(COSMOS)

    def _value(self, value: Any) -> str:
        self.params.append(to_json(value))
        return f"@p{len(self.params) - 1}"

    def condition(self, node: Any) -> str:
        if isinstance(node, Mapping) and len(node) == 1:
            op, args = next(iter(node.items()))
            if op in ("eq", "ne") and None in args:
                operand = args[0] if args[1] is None else args[1]
                column, _ = self._operand(operand)
                missing = f"(NOT IS_DEFINED({column}) OR IS_NULL({column}))"
                return missing if op == "eq" else f"(NOT {missing})"
            if op == "not":  # Cosmos binds NOT tighter than comparisons
                return f"(NOT ({self.condition(args)}))"
            if op == "exists":
                column, _ = self._operand(args)
                return f"(IS_DEFINED({column}) AND NOT IS_NULL({column}))"
        return super().condition(node).replace("1 = 1", "true").replace("1 = 0", "false")


def _ids_as_text(node: Any, id_field: str) -> Any:
    """Values compared with the id field as strings (Cosmos ids are text)."""
    if not isinstance(node, Mapping) or len(node) != 1:
        return node
    op, args = next(iter(node.items()))
    if op in ("and", "or"):
        return {op: [_ids_as_text(a, id_field) for a in args]}
    if op == "not":
        return {op: _ids_as_text(args, id_field)}
    target = {"attr": f"resource.{id_field}"}
    if op in ("eq", "ne", "lt", "le", "gt", "ge") and target in args:
        return {op: [a if a == target or a is None else str(a) for a in args]}
    if op in ("in", "nin") and args[0] == target:
        return {op: [args[0], [str(v) for v in args[1]]]}
    return node


# ---------------------------------------------------------------- the store


@dataclass(frozen=True)
class CosmosContainer:
    """A container holding one resource.

    Args:
        name: The container.
        partition_key: The field the container is partitioned by (its
            partition key path without the slash); the id by default.
    """

    name: str
    partition_key: str | None = None


def _failed(operation: str, exc: Exception) -> RemoteCallFailed:
    return RemoteCallFailed(
        f"Cosmos DB {operation} failed: {exc}",
        service="cosmos",
        operation=operation,
        status=getattr(exc, "status_code", None),
    )


def _clean(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


class CosmosRepository(DocumentRepository):
    supports_conditions = True
    supports_versions = True

    def __init__(
        self,
        uow: CosmosUnitOfWork,
        resource: str,
        codec: DocumentCodec,
        *,
        version_field: str | None,
        sortable: Iterable[str] | None,
    ) -> None:
        super().__init__(uow, resource, codec, version_field=version_field)
        self.store = uow.store
        self.container = uow.store.container(resource)
        self.partition_key = uow.store.partition_keys[resource]
        self.sortable = frozenset(sortable) if sortable is not None else None

    async def fetch(self, key: Any) -> dict[str, Any] | None:
        from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

        if self.partition_key == self.codec.id_field:
            try:
                return _clean(await self.container.read_item(str(key), partition_key=str(key)))
            except CosmosResourceNotFoundError:
                return None
            except CosmosHttpResponseError as exc:
                raise _failed("read_item", exc) from exc
        documents, _ = await self.query(
            ListQuery(filters={self.codec.id_field: key}, limit=1), count=False
        )
        return documents[0] if documents else None

    async def query(
        self, query: ListQuery, *, count: bool = True
    ) -> tuple[list[dict[str, Any]], int]:
        from azure.cosmos.exceptions import CosmosHttpResponseError

        if query.translatable & {*query.filters, *(o.lstrip("-") for o in query.order_by)}:
            raise ConditionNotSupported(
                f"Cosmos DB can't filter or sort {self.resource} by translatable fields"
            )
        id_field = self.codec.id_field
        filters = {
            k: (str(v) if k == id_field and v is not None else v) for k, v in query.filters.items()
        }
        condition = _ids_as_text(query.condition, id_field) if query.condition is not None else None
        scoped = ListQuery(filters=filters, condition=condition)
        compiler = CosmosCompiler()
        where = compiler.where(scoped)
        params = [{"name": f"@p{i}", "value": v} for i, v in enumerate(compiler.params)]
        order = compiler.order(query, None)
        sql = f"SELECT * FROM c{where}{order} OFFSET {int(query.offset)} LIMIT {int(query.limit)}"
        try:
            documents = [
                _clean(i) async for i in self.container.query_items(sql, parameters=params)
            ]
            total = len(documents)
            if count:
                counted = [
                    n
                    async for n in self.container.query_items(
                        f"SELECT VALUE COUNT(1) FROM c{where}", parameters=params
                    )
                ]
                total = int(sum(counted))
        except CosmosHttpResponseError as exc:
            raise _failed("query", exc) from exc
        return documents, total


class CosmosUnitOfWork(DocumentUnitOfWork):
    transaction_scope = "partition"
    max_writes = None  # per partition: 100, checked per batch

    def __init__(self, store: CosmosStore) -> None:
        super().__init__()
        self.store = store

    def make_repository(self, resource_name: str) -> CosmosRepository:
        if resource_name not in self.store.containers:
            raise KeyError(f"No Cosmos DB container for resource {resource_name!r}")
        return CosmosRepository(
            self,
            resource_name,
            self.store.codecs[resource_name],
            version_field=self.store.version_fields.get(resource_name),
            sortable=self.store.sortable.get(resource_name),
        )

    def _partition(self, write: Write) -> tuple[str, Any]:
        field = self.store.partition_keys[write.resource]
        codec = self.store.codecs[write.resource]
        if field == codec.id_field:
            return write.resource, str(write.key)
        return write.resource, to_json((write.document or {}).get(field))

    def item(self, write: Write) -> dict[str, Any]:
        document = to_json(write.document or {})
        document["id"] = str(write.key)
        return document

    async def apply(self, writes: Sequence[Write]) -> None:
        from azure.cosmos.exceptions import CosmosBatchOperationError, CosmosHttpResponseError
        from resource_engine.errors import TransactionTooLarge

        batches: dict[tuple[str, Any], list[Write]] = {}
        for write in writes:
            batches.setdefault(self._partition(write), []).append(write)
        for group in batches.values():
            if len(group) > 100:
                raise TransactionTooLarge(
                    f"{len(group)} writes to one Cosmos DB partition; a batch takes 100",
                    details={"writes": len(group), "max_writes": 100},
                )
        for (resource, partition), group in batches.items():
            container = self.store.container(resource)
            operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
            for write in group:
                etag: dict[str, Any] = {}
                if write.expected_version is not None:
                    try:
                        stored = await container.read_item(str(write.key), partition_key=partition)
                    except CosmosHttpResponseError as exc:
                        if exc.status_code == 404:
                            raise version_conflict(write) from exc
                        raise _failed("read_item", exc) from exc
                    if stored.get(write.version_field) != write.expected_version:
                        raise version_conflict(write, stored.get(write.version_field))
                    etag = {"if_match_etag": stored["_etag"]}
                if write.kind == "delete":
                    operations.append(("delete", (str(write.key),), etag))
                elif write.create:
                    operations.append(("create", (self.item(write),), {}))
                elif etag:
                    operations.append(("replace", (str(write.key), self.item(write)), etag))
                else:
                    operations.append(("upsert", (self.item(write),), {}))
            try:
                await container.execute_item_batch(operations, partition_key=partition)
            except CosmosBatchOperationError as exc:
                failed = group[exc.error_index] if exc.error_index < len(group) else group[0]
                status = exc.operation_responses[exc.error_index].get("statusCode")
                if status in (409, 412, 404):
                    raise version_conflict(failed) from exc
                raise _failed("batch", exc) from exc
            except CosmosHttpResponseError as exc:
                raise _failed("batch", exc) from exc


class CosmosStore:
    """Cosmos DB containers as repositories.

    Args:
        database: ``azure.cosmos.aio.DatabaseProxy`` (the client owned by the caller).
        registry: The registry (models, id and version fields).
        containers: Resource name → container name or ``CosmosContainer``.
        sortable: Resource name → the fields indexed for sorting; others are
            refused. Leave a resource out to allow any sort.
    """

    def __init__(
        self,
        database: Any,
        registry: Any,
        containers: Mapping[str, str | CosmosContainer],
        *,
        sortable: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.database = database
        self.sortable = dict(sortable or {})
        self.containers: dict[str, CosmosContainer] = {}
        self.partition_keys: dict[str, str] = {}
        self.codecs: dict[str, DocumentCodec] = {}
        self.version_fields: dict[str, str] = {}
        self._clients: dict[str, Any] = {}
        for name, container in containers.items():
            definition = registry.get(name)
            spec = CosmosContainer(container) if isinstance(container, str) else container
            self.containers[name] = spec
            self.partition_keys[name] = spec.partition_key or definition.id_field
            self.codecs[name] = DocumentCodec(definition.model, id_field=definition.id_field)
            if definition.version_field:
                self.version_fields[name] = definition.version_field

    def container(self, resource: str) -> Any:
        if resource not in self._clients:
            self._clients[resource] = self.database.get_container_client(
                self.containers[resource].name
            )
        return self._clients[resource]

    def unit_of_work(self) -> CosmosUnitOfWork:
        return CosmosUnitOfWork(self)


__all__ = [
    "COSMOS",
    "CosmosCompiler",
    "CosmosContainer",
    "CosmosRepository",
    "CosmosStore",
    "CosmosUnitOfWork",
    "to_json",
]
