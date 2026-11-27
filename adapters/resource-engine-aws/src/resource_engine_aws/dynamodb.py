"""Amazon DynamoDB repositories (extra ``[dynamodb]``).

```python
from resource_engine_aws import DynamoDbStore, DynamoTable

async with session.create_client("dynamodb", region_name="eu-west-1") as dynamodb:
    store = DynamoDbStore(dynamodb, registry, {
        "partners": "partners",                                   # key "id"
        "orders": DynamoTable("orders", indexes={"partner_id": "by-partner"}),
    })
    manager = ResourceManager(registry, store.unit_of_work)
```

- **Writes** go in one ``TransactWriteItems`` at commit (up to 100 items, all
  or nothing). Creates are conditional on the id being free and versioned
  writes on the stored version, so racing writers get ``VersionConflict``.
- **Lists** read by key when the id is filtered, ``Query`` a global secondary
  index when a filter names an indexed field (``indexes``), and ``Scan``
  otherwise. Filters, row filters, sorting and paging are then applied in
  memory, so a list reads every matching item: past ``max_items`` it fails
  with ``QueryNotSupported`` instead of scanning on. Index the fields lists
  filter by, or keep these tables small.
- **Types:** numbers (``int``, ``Decimal``, ``float``) are DynamoDB numbers;
  dates and timestamps ISO 8601 strings (UTC), so they sort as text.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from resource_engine.application.query import ListQuery, Page
from resource_engine.errors import QueryNotSupported, RemoteCallFailed
from resource_engine.nosql import (
    DocumentCodec,
    DocumentRepository,
    DocumentUnitOfWork,
    Write,
    select,
    version_conflict,
)

# ------------------------------------------------------------------ types


def to_attribute(value: Any) -> dict[str, Any]:
    """A Python value as a DynamoDB attribute value."""
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, float):
        return {"N": repr(value)}
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value
        return {"S": moment.isoformat()}
    if isinstance(value, date):
        return {"S": value.isoformat()}
    if isinstance(value, enum.Enum):
        return to_attribute(value.value)
    if isinstance(value, (bytes, bytearray)):
        return {"B": bytes(value)}
    if isinstance(value, Mapping):
        return {"M": {str(k): to_attribute(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"L": [to_attribute(v) for v in value]}
    return {"S": str(value)}  # str, UUID...


def from_attribute(attribute: Mapping[str, Any]) -> Any:
    (kind, value), *_ = attribute.items()
    if kind == "NULL":
        return None
    if kind == "N":
        return int(value) if value.lstrip("-").isdigit() else Decimal(value)
    if kind == "M":
        return {k: from_attribute(v) for k, v in value.items()}
    if kind == "L":
        return [from_attribute(v) for v in value]
    if kind in ("SS", "BS"):
        return list(value)
    if kind == "NS":
        return [Decimal(v) for v in value]
    return value  # S, B, BOOL


def to_item(document: Mapping[str, Any]) -> dict[str, Any]:
    return {k: to_attribute(v) for k, v in document.items()}


def from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: from_attribute(v) for k, v in item.items()}


# ------------------------------------------------------------------ store


@dataclass(frozen=True)
class DynamoTable:
    """A table holding one resource.

    Args:
        name: The table.
        key: The partition key attribute: the resource's id field.
        indexes: Field → global secondary index whose partition key is that
            field, used when a list filters by it.
    """

    name: str
    key: str = "id"
    indexes: Mapping[str, str] = field(default_factory=dict)


def _failed(operation: str, exc: Exception) -> RemoteCallFailed:
    return RemoteCallFailed(
        f"DynamoDB {operation} failed: {exc}", service="dynamodb", operation=operation
    )


class DynamoDbRepository(DocumentRepository):
    supports_conditions = True
    supports_versions = True

    def __init__(
        self,
        uow: DynamoDbUnitOfWork,
        resource: str,
        table: DynamoTable,
        codec: DocumentCodec,
        *,
        version_field: str | None,
        max_items: int,
    ) -> None:
        super().__init__(uow, resource, codec, version_field=version_field)
        self.client = uow.client
        self.table = table
        self.max_items = max_items

    def key(self, value: Any) -> dict[str, Any]:
        return {self.table.key: to_attribute(value)}

    async def fetch(self, key: Any) -> dict[str, Any] | None:
        try:
            response = await self.client.get_item(
                TableName=self.table.name, Key=self.key(key), ConsistentRead=True
            )
        except Exception as exc:
            raise _failed("get_item", exc) from exc
        item = response.get("Item")
        return from_item(item) if item else None

    async def _read(self, query: ListQuery) -> list[dict[str, Any]]:
        """The items that may match: by key, by an index, or the whole table."""
        filters = query.filters
        if self.table.key in filters:
            found = await self.fetch(filters[self.table.key])
            return [found] if found is not None else []
        request: dict[str, Any] = {"TableName": self.table.name}
        operation = "scan"
        for name, index in self.table.indexes.items():
            if name in filters and name not in query.translatable:
                operation = "query"
                request |= {
                    "IndexName": index,
                    "KeyConditionExpression": "#k = :k",
                    "ExpressionAttributeNames": {"#k": name},
                    "ExpressionAttributeValues": {":k": to_attribute(filters[name])},
                }
                break
        items: list[dict[str, Any]] = []
        call = self.client.query if operation == "query" else self.client.scan
        try:
            while True:
                response = await call(**request)
                items.extend(from_item(i) for i in response.get("Items", []))
                if len(items) > self.max_items:
                    raise QueryNotSupported(
                        f"Listing {self.resource} reads more than {self.max_items} items",
                        hint="Filter by the id or an indexed field (DynamoTable(indexes=...)).",
                        details={"max_items": self.max_items, "operation": operation},
                    )
                following = response.get("LastEvaluatedKey")
                if not following:
                    return items
                request["ExclusiveStartKey"] = following
        except QueryNotSupported:
            raise
        except Exception as exc:
            raise _failed(operation, exc) from exc

    async def list(self, query: ListQuery) -> Page[Any]:
        entities = [self.codec.decode(d) for d in await self._read(query)]
        items, total = select(entities, query)
        return Page(items, total, query.offset, query.limit)


class DynamoDbUnitOfWork(DocumentUnitOfWork):
    transaction_scope = "unit_of_work"
    max_writes = 100

    def __init__(self, store: DynamoDbStore) -> None:
        super().__init__()
        self.store = store
        self.client = store.client

    def make_repository(self, resource_name: str) -> DynamoDbRepository:
        try:
            table = self.store.tables[resource_name]
        except KeyError:
            raise KeyError(f"No DynamoDB table configured for resource {resource_name!r}") from None
        return DynamoDbRepository(
            self,
            resource_name,
            table,
            self.store.codecs[resource_name],
            version_field=self.store.version_fields.get(resource_name),
            max_items=self.store.max_items,
        )

    def item(self, write: Write) -> dict[str, Any]:
        table = self.store.tables[write.resource]
        names: dict[str, str] = {}
        values: dict[str, Any] = {}
        conditions: list[str] = []
        if write.create:
            names["#k"] = table.key
            conditions.append("attribute_not_exists(#k)")
        if write.expected_version is not None:
            names["#v"] = write.version_field
            values[":v"] = to_attribute(write.expected_version)
            conditions.append("#v = :v")
        if write.kind == "put":
            body: dict[str, Any] = {"TableName": table.name, "Item": to_item(write.document or {})}
        else:
            body = {"TableName": table.name, "Key": {table.key: to_attribute(write.key)}}
        if conditions:
            body["ConditionExpression"] = " AND ".join(conditions)
            body["ExpressionAttributeNames"] = names
            if values:
                body["ExpressionAttributeValues"] = values
            body["ReturnValuesOnConditionCheckFailure"] = "ALL_OLD"
        return {"Put" if write.kind == "put" else "Delete": body}

    async def apply(self, writes: Sequence[Write]) -> None:
        try:
            await self.client.transact_write_items(
                TransactItems=[self.item(w) for w in writes], ClientRequestToken=uuid.uuid4().hex
            )
        except Exception as exc:
            response = getattr(exc, "response", {}) or {}
            if response.get("Error", {}).get("Code") == "TransactionCanceledException":
                reasons = response.get("CancellationReasons") or []
                for write, reason in zip(writes, reasons, strict=False):
                    if reason.get("Code") == "ConditionalCheckFailed":
                        old = from_item(reason.get("Item") or {})
                        raise version_conflict(write, old.get(write.version_field)) from exc
            raise _failed("transact_write_items", exc) from exc


class DynamoDbStore:
    """DynamoDB tables as repositories.

    Args:
        client: An aiobotocore ``dynamodb`` client (owned by the caller).
        registry: The registry (models, id and version fields).
        tables: Resource name → table name or ``DynamoTable``.
        max_items: The most items a list may read before it is refused.
    """

    def __init__(
        self,
        client: Any,
        registry: Any,
        tables: Mapping[str, str | DynamoTable],
        *,
        max_items: int = 10_000,
    ) -> None:
        self.client = client
        self.max_items = max_items
        self.tables: dict[str, DynamoTable] = {}
        self.codecs: dict[str, DocumentCodec] = {}
        self.version_fields: dict[str, str] = {}
        for name, table in tables.items():
            definition = registry.get(name)
            self.tables[name] = (
                DynamoTable(table, key=definition.id_field) if isinstance(table, str) else table
            )
            self.codecs[name] = DocumentCodec(definition.model, id_field=definition.id_field)
            if definition.version_field:
                self.version_fields[name] = definition.version_field

    def unit_of_work(self) -> DynamoDbUnitOfWork:
        return DynamoDbUnitOfWork(self)


__all__ = [
    "DynamoDbRepository",
    "DynamoDbStore",
    "DynamoDbUnitOfWork",
    "DynamoTable",
    "from_item",
    "to_item",
]
