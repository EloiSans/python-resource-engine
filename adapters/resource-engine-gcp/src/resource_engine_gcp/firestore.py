"""Firestore repositories (extra ``[firestore]``), over the REST API with ``httpx``.

```python
from resource_engine_gcp import FirestoreStore, gcs_token

store = FirestoreStore(http, registry, project="acme",
                       collections={"partners": "partners"},
                       token=gcs_token(["https://www.googleapis.com/auth/datastore"]))
manager = ResourceManager(registry, store.unit_of_work)
```

- **Writes** go in one commit (up to 500, all or nothing). Creates require
  the document not to exist. Versioned writes run in a transaction that
  reads the stored versions first, so racing writers get ``VersionConflict``.
- **Lists** are Firestore queries: filters and row filters become ``where``
  (``not`` is pushed into the comparisons; ``exists`` is ``!= null``),
  ``orderBy``, ``offset`` and ``limit``, with a ``count()`` aggregation for
  the total. Queries that filter and sort on different fields need a
  composite index; name the sortable fields you indexed with ``sortable=``
  and other sorts are refused (``QueryNotSupported``) instead of failing in
  Firestore. Firestore's ``!=`` and ``not-in`` leave out documents without
  the field.
- **Types:** ``Decimal`` values are stored as doubles (exact amounts: use
  integer cents); timestamps as timestamps; dates as ISO 8601 strings.
"""

from __future__ import annotations

import base64
import enum
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx
from resource_engine.application.query import ListQuery
from resource_engine.errors import ConditionNotSupported, RemoteCallFailed
from resource_engine.nosql import (
    DocumentCodec,
    DocumentRepository,
    DocumentUnitOfWork,
    Write,
    field_of,
    version_conflict,
    without_not,
)

type TokenProvider = Callable[[], Awaitable[str | None]]

# ------------------------------------------------------------------ values


def to_value(value: Any) -> dict[str, Any]:
    """A Python value as a Firestore ``Value``."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, (float, Decimal)):
        return {"doubleValue": float(value)}
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return {"timestampValue": moment.isoformat().replace("+00:00", "Z")}
    if isinstance(value, date):
        return {"stringValue": value.isoformat()}
    if isinstance(value, enum.Enum):
        return to_value(value.value)
    if isinstance(value, (bytes, bytearray)):
        return {"bytesValue": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, Mapping):
        return {"mapValue": {"fields": {str(k): to_value(v) for k, v in value.items()}}}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {"arrayValue": {"values": [to_value(v) for v in value]}}
    return {"stringValue": str(value)}


def from_value(value: Mapping[str, Any]) -> Any:
    (kind, data), *_ = value.items()
    if kind == "nullValue":
        return None
    if kind == "integerValue":
        return int(data)
    if kind == "timestampValue":
        return datetime.fromisoformat(data.replace("Z", "+00:00"))
    if kind == "bytesValue":
        return base64.b64decode(data)
    if kind == "mapValue":
        return {k: from_value(v) for k, v in (data.get("fields") or {}).items()}
    if kind == "arrayValue":
        return [from_value(v) for v in (data.get("values") or [])]
    return data  # string, boolean, double, reference, geoPoint


def _fields(document: Mapping[str, Any]) -> dict[str, Any]:
    return {k: to_value(v) for k, v in document.items()}


# ------------------------------------------------------------- the queries

_OPERATORS = {
    "eq": "EQUAL",
    "ne": "NOT_EQUAL",
    "lt": "LESS_THAN",
    "le": "LESS_THAN_OR_EQUAL",
    "gt": "GREATER_THAN",
    "ge": "GREATER_THAN_OR_EQUAL",
    "in": "IN",
    "nin": "NOT_IN",
}
_FLIPPED = {"lt": "gt", "le": "ge", "gt": "lt", "ge": "le", "eq": "eq", "ne": "ne"}


def _field_filter(name: str, op: str, value: Any) -> dict[str, Any]:
    return {"fieldFilter": {"field": {"fieldPath": name}, "op": op, "value": to_value(value)}}


def _composite(op: str, parts: list[Any]) -> Any:
    """``AND``/``OR`` of compiled parts, simplifying ``True``/``False``."""
    absorbing, neutral = (False, True) if op == "AND" else (True, False)
    if any(p is absorbing for p in parts):
        return absorbing
    filters = [p for p in parts if p is not neutral]
    if not filters:
        return neutral
    if len(filters) == 1:
        return filters[0]
    return {"compositeFilter": {"op": op, "filters": filters}}


def compile_condition(node: Any) -> Any:
    """A condition as a Firestore filter (or ``True``/``False``)."""
    node = without_not(node)
    if isinstance(node, bool):
        return node
    if not isinstance(node, Mapping) or len(node) != 1:
        raise ConditionNotSupported(f"Unsupported condition {node!r}")
    op, args = next(iter(node.items()))
    if op in ("and", "or"):
        return _composite(op.upper(), [compile_condition(a) for a in args])
    if op == "exists":
        name = field_of(args)
        if name is None:
            raise ConditionNotSupported(f"exists needs a record field, not {args!r}")
        return {"unaryFilter": {"field": {"fieldPath": name}, "op": "IS_NOT_NULL"}}
    if op in _FLIPPED:
        left, right = args
        name, value = field_of(left), right
        if name is None:
            name, value, op = field_of(right), left, _FLIPPED[op]
        if name is None or field_of(value) is not None:
            raise ConditionNotSupported("Firestore compares a record field with a value")
        if value is None and op in ("eq", "ne"):
            unary = "IS_NULL" if op == "eq" else "IS_NOT_NULL"
            return {"unaryFilter": {"field": {"fieldPath": name}, "op": unary}}
        return _field_filter(name, _OPERATORS[op], value)
    if op in ("in", "nin"):
        left, values = args
        name = field_of(left)
        if name is None:
            raise ConditionNotSupported(f"{op} needs a record field, not {left!r}")
        if not values:
            return op == "nin"
        return _field_filter(name, _OPERATORS[op], list(values))
    if op == "contains":
        collection, item = args
        name = field_of(collection)
        if name is None:
            raise ConditionNotSupported("contains needs a record field (an array)")
        return _field_filter(name, "ARRAY_CONTAINS", item)
    raise ConditionNotSupported(f"Firestore queries can't express {op!r}")


_INEQUALITIES = {
    "NOT_EQUAL",
    "LESS_THAN",
    "LESS_THAN_OR_EQUAL",
    "GREATER_THAN",
    "GREATER_THAN_OR_EQUAL",
    "NOT_IN",
    "IS_NOT_NULL",
}


def _inequality_fields(node: Any) -> list[str]:
    if not isinstance(node, Mapping):
        return []
    if "compositeFilter" in node:
        return [n for f in node["compositeFilter"]["filters"] for n in _inequality_fields(f)]
    leaf = node.get("fieldFilter") or node.get("unaryFilter") or {}
    return [leaf["field"]["fieldPath"]] if leaf.get("op") in _INEQUALITIES else []


def compile_where(query: ListQuery) -> Any:
    parts: list[Any] = [_field_filter(k, "EQUAL", v) for k, v in query.filters.items()]
    if query.condition is not None:
        parts.append(compile_condition(query.condition))
    return _composite("AND", parts)


# ---------------------------------------------------------------- the store


class FirestoreRepository(DocumentRepository):
    supports_conditions = True
    supports_versions = True

    def __init__(
        self,
        uow: FirestoreUnitOfWork,
        resource: str,
        collection: str,
        codec: DocumentCodec,
        *,
        version_field: str | None,
        sortable: Iterable[str] | None,
    ) -> None:
        super().__init__(uow, resource, codec, version_field=version_field)
        self.store = uow.store
        self.collection = collection
        self.sortable = frozenset(sortable) if sortable is not None else None

    async def fetch(self, key: Any) -> dict[str, Any] | None:
        response = await self.store.request(
            "GET", f"{self.store.documents}/{self.collection}/{quote(str(key), safe='')}", "get"
        )
        if response.status_code == 404:
            return None
        self.store.check(response, "get")
        return {k: from_value(v) for k, v in (response.json().get("fields") or {}).items()}

    async def query(self, query: ListQuery) -> tuple[list[dict[str, Any]], int]:
        if query.translatable & ({*query.filters, *(o.lstrip("-") for o in query.order_by)}):
            raise ConditionNotSupported(
                f"Firestore can't filter or sort {self.resource} by translatable fields"
            )
        where = compile_where(query)
        if where is False:
            return [], 0
        structured: dict[str, Any] = {"from": [{"collectionId": self.collection}]}
        if where is not True:
            structured["where"] = where
        count = await self.store.request(
            "POST",
            f"{self.store.documents}:runAggregationQuery",
            "count",
            json={
                "structuredAggregationQuery": {
                    "structuredQuery": structured,
                    "aggregations": [{"alias": "total", "count": {}}],
                }
            },
        )
        self.store.check(count, "count")
        result = next((r["result"] for r in count.json() if "result" in r), None)
        total = int(result["aggregateFields"]["total"]["integerValue"]) if result else 0
        orders = [
            {
                "field": {"fieldPath": o.lstrip("-")},
                "direction": "DESCENDING" if o.startswith("-") else "ASCENDING",
            }
            for o in query.order_by
        ]
        ordered = {o.lstrip("-") for o in query.order_by}
        for name in _inequality_fields(where):  # Firestore orders by them before the key
            if name not in ordered:
                ordered.add(name)
                orders.append({"field": {"fieldPath": name}, "direction": "ASCENDING"})
        orders.append({"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"})
        structured |= {"orderBy": orders, "offset": query.offset, "limit": query.limit}
        response = await self.store.request(
            "POST",
            f"{self.store.documents}:runQuery",
            "query",
            json={"structuredQuery": structured},
        )
        self.store.check(response, "query")
        documents = [
            {k: from_value(v) for k, v in (r["document"].get("fields") or {}).items()}
            for r in response.json()
            if "document" in r
        ]
        return documents, total


class FirestoreUnitOfWork(DocumentUnitOfWork):
    transaction_scope = "unit_of_work"
    max_writes = 500

    def __init__(self, store: FirestoreStore) -> None:
        super().__init__()
        self.store = store

    def make_repository(self, resource_name: str) -> FirestoreRepository:
        try:
            collection = self.store.collections[resource_name]
        except KeyError:
            raise KeyError(f"No Firestore collection for resource {resource_name!r}") from None
        return FirestoreRepository(
            self,
            resource_name,
            collection,
            self.store.codecs[resource_name],
            version_field=self.store.version_fields.get(resource_name),
            sortable=self.store.sortable.get(resource_name),
        )

    def name(self, write: Write) -> str:
        collection = self.store.collections[write.resource]
        return f"{self.store.database}/documents/{collection}/{write.key}"

    def write(self, write: Write) -> dict[str, Any]:
        if write.kind == "delete":
            return {"delete": self.name(write)}
        body: dict[str, Any] = {
            "update": {"name": self.name(write), "fields": _fields(write.document or {})}
        }
        if write.create:
            body["currentDocument"] = {"exists": False}
        return body

    async def apply(self, writes: Sequence[Write]) -> None:
        body: dict[str, Any] = {"writes": [self.write(w) for w in writes]}
        versioned = [w for w in writes if w.expected_version is not None]
        if versioned:
            begin = await self.store.request(
                "POST", f"{self.store.documents}:beginTransaction", "begin", json={}
            )
            self.store.check(begin, "begin")
            transaction = begin.json()["transaction"]
            read = await self.store.request(
                "POST",
                f"{self.store.documents}:batchGet",
                "batch_get",
                json={"documents": [self.name(w) for w in versioned], "transaction": transaction},
            )
            self.store.check(read, "batch_get")
            stored = {
                r["found"]["name"].rsplit("/", 1)[-1]: r["found"].get("fields") or {}
                for r in read.json()
                if "found" in r
            }
            for w in versioned:
                fields = stored.get(str(w.key))
                current = (
                    from_value(fields[w.version_field])
                    if fields and w.version_field in fields
                    else None
                )
                if current != w.expected_version:
                    await self.store.request(
                        "POST",
                        f"{self.store.documents}:rollback",
                        "rollback",
                        json={"transaction": transaction},
                    )
                    raise version_conflict(w, current)
            body["transaction"] = transaction
        response = await self.store.request(
            "POST", f"{self.store.documents}:commit", "commit", json=body
        )
        if response.status_code in (400, 409) and (
            "ALREADY_EXISTS" in response.text or "FAILED_PRECONDITION" in response.text
        ):  # a create whose id is taken
            created = next((w for w in writes if w.create), writes[0])
            raise version_conflict(created)
        self.store.check(response, "commit")


class FirestoreStore:
    """Firestore collections as repositories.

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        registry: The registry (models, id and version fields).
        project / database: The Firestore database (``(default)``).
        collections: Resource name → collection (top level).
        token: Async function returning a bearer token (``gcs_token`` with the
            ``datastore`` scope); ``None`` for the emulator.
        url: The API (``https://firestore.googleapis.com``, or the emulator's).
        sortable: Resource name → the fields with indexes for sorting; others
            are refused. Leave a resource out to allow any sort.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        registry: Any,
        *,
        project: str,
        collections: Mapping[str, str],
        database: str = "(default)",
        token: TokenProvider | None = None,
        url: str = "https://firestore.googleapis.com",
        sortable: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.client = client
        self.token = token
        self.database = f"projects/{project}/databases/{database}"
        self.documents = f"{url.rstrip('/')}/v1/{self.database}/documents"
        self.collections = dict(collections)
        self.sortable = dict(sortable or {})
        self.codecs: dict[str, DocumentCodec] = {}
        self.version_fields: dict[str, str] = {}
        for name in self.collections:
            definition = registry.get(name)
            self.codecs[name] = DocumentCodec(definition.model, id_field=definition.id_field)
            if definition.version_field:
                self.version_fields[name] = definition.version_field

    async def request(self, method: str, url: str, operation: str, **kw: Any) -> httpx.Response:
        token = await self.token() if self.token else "owner"  # the emulator takes "owner"
        try:
            return await self.client.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, **kw
            )
        except httpx.HTTPError as exc:
            raise RemoteCallFailed(
                f"Firestore {operation} failed: {exc}", service="firestore", operation=operation
            ) from exc

    def check(self, response: httpx.Response, operation: str) -> None:
        if response.status_code >= 400:
            raise RemoteCallFailed(
                f"Firestore {operation} failed: HTTP {response.status_code} {response.text[:300]}",
                service="firestore",
                operation=operation,
                status=response.status_code,
                body=response.text[:300],
            )

    def unit_of_work(self) -> FirestoreUnitOfWork:
        return FirestoreUnitOfWork(self)


__all__ = [
    "FirestoreRepository",
    "FirestoreStore",
    "FirestoreUnitOfWork",
    "compile_condition",
    "from_value",
    "to_value",
]
