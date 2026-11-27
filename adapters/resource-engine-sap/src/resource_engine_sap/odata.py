"""OData v2 and v4 repositories for SAP APIs (S/4HANA, SuccessFactors, BTP
services), on top of the REST repository.

```python
from resource_engine_sap import ODataEndpoint, destination_client, odata_unit_of_work_factory

partners = ODataEndpoint.for_dataclass(
    Partner,
    "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner",
    version="v2",
    properties={"id": "BusinessPartner", "name": "BusinessPartnerFullName",
                "created_on": "CreationDate"},
)
http = await destination_client(destinations, "S4HANA")   # or any authenticated client
manager = ResourceManager(registry, odata_unit_of_work_factory(http, {"partners": partners}))
```

- **Queries:** filters and row filters become ``$filter`` (``eq``, ``ne``,
  ``lt``... ``and``/``or``/``not``; ``in`` as ``in (...)`` in v4 and ``or``
  in v2), sorting ``$orderby``, paging ``$skip``/``$top``, the total
  ``$inlinecount=allpages`` (v2) or ``$count=true`` (v4). Only the mapped
  properties are read (``$select``).
- **Values:** v2 dates (``/Date(ms)/``) and decimals (strings) are read back
  as the model's types; writes send them in the version's format (v4 with
  ``IEEE754Compatible=true``, so decimals stay exact).
- **Writes** fetch the CSRF token SAP gateways require (and fetch it again
  when it expires), and send the ETag read with the record as ``If-Match``
  (``*`` when none was read). A 412 is a ``VersionConflict``.
- **Not transactional:** each write is its own request (no ``$batch``
  change sets). Use compensations in a composite store when an operation also
  writes elsewhere.
"""

from __future__ import annotations

import dataclasses
import enum
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import httpx
from resource_engine.application.query import ListQuery, Page
from resource_engine.errors import ConditionNotSupported, VersionConflict
from resource_engine.nosql import DocumentCodec, field_of
from resource_engine_http.rest import RemoteUnitOfWork, RestEndpoint, RestRepository

type Json = dict[str, Any]
type Version = Literal["v2", "v4"]

# ------------------------------------------------------------------ values

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _v2_date(value: datetime | date) -> str:
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    moment = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    return f"/Date({int((moment - _EPOCH).total_seconds() * 1000)})/"


def read_value(value: Any) -> Any:
    """An OData JSON value as Python (v2 ``/Date(ms)/`` → ``datetime``)."""
    if isinstance(value, str) and value.startswith("/Date(") and value.endswith(")/"):
        inner = value[6:-2]
        sign = max(inner.rfind("+"), inner.rfind("-"))
        millis = int(inner[:sign] if sign > 0 else inner)
        return datetime.fromtimestamp(millis / 1000, UTC)
    return value


def write_value(value: Any, version: Version) -> Any:
    """A Python value as OData JSON for ``version``."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)) and version == "v2":
        return _v2_date(value)
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {k: write_value(v, version) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [write_value(v, version) for v in value]
    if isinstance(value, enum.Enum):
        return value.value
    return value


def literal(value: Any, version: Version) -> str:
    """A Python value as an OData URL literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return f"{value}M" if version == "v2" else str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        if version == "v2":
            return f"datetime'{moment.replace(tzinfo=None).isoformat(timespec='seconds')}'"
        return moment.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return f"datetime'{value.isoformat()}T00:00:00'" if version == "v2" else value.isoformat()
    if isinstance(value, UUID):
        return f"guid'{value}'" if version == "v2" else str(value)
    text = str(getattr(value, "value", value))
    return "'" + text.replace("'", "''") + "'"


# ----------------------------------------------------------------- queries

_COMPARISONS = {"eq": "eq", "ne": "ne", "lt": "lt", "le": "le", "gt": "gt", "ge": "ge"}
_FLIPPED = {"eq": "eq", "ne": "ne", "lt": "gt", "le": "ge", "gt": "lt", "ge": "le"}


class FilterCompiler:
    """Filters and the condition language as an OData ``$filter``."""

    def __init__(self, version: Version, properties: Mapping[str, str]) -> None:
        self.version = version
        self.properties = properties

    def prop(self, name: str) -> str:
        try:
            return self.properties[name]
        except KeyError:
            raise ConditionNotSupported(f"{name!r} is not mapped to an OData property") from None

    def condition(self, node: Any) -> str:
        if node is True:
            return "true"
        if node is False:
            return "false"
        if not isinstance(node, Mapping) or len(node) != 1:
            raise ConditionNotSupported(f"Unsupported condition {node!r}")
        op, args = next(iter(node.items()))
        if op in ("and", "or"):
            parts = [self.condition(a) for a in args]
            if not parts:
                return "true" if op == "and" else "false"
            return "(" + f" {op} ".join(parts) + ")"
        if op == "not":
            return f"not ({self.condition(args)})"
        if op == "exists":
            name = field_of(args)
            if name is None:
                raise ConditionNotSupported("exists needs a record field")
            return f"{self.prop(name)} ne null"
        if op in _COMPARISONS:
            left, right = args
            name, value = field_of(left), right
            if name is None:
                name, value, op = field_of(right), left, _FLIPPED[op]
            if name is None or field_of(value) is not None:
                raise ConditionNotSupported("OData filters compare a record field with a value")
            return f"{self.prop(name)} {_COMPARISONS[op]} {literal(value, self.version)}"
        if op in ("in", "nin"):
            left, values = args
            name = field_of(left)
            if name is None:
                raise ConditionNotSupported(f"{op} needs a record field")
            if not values:
                return "false" if op == "in" else "true"
            prop = self.prop(name)
            if self.version == "v4":
                listed = ",".join(literal(v, self.version) for v in values)
                found = f"{prop} in ({listed})"
            else:
                found = "(" + " or ".join(f"{prop} eq {literal(v, 'v2')}" for v in values) + ")"
            return found if op == "in" else f"not ({found})"
        raise ConditionNotSupported(f"OData filters can't express {op!r}")

    def filter(self, query: ListQuery) -> str | None:
        parts = [f"{self.prop(k)} eq {literal(v, self.version)}" for k, v in query.filters.items()]
        if query.condition is not None:
            parts.append(self.condition(query.condition))
        return " and ".join(parts) if parts else None


# ---------------------------------------------------------------- endpoint


@dataclass(frozen=True)
class ODataEndpoint[E](RestEndpoint[E]):
    """An OData entity set mapped to a resource.

    Args (besides ``RestEndpoint``'s):
        version: ``"v2"`` or ``"v4"``.
        properties: Model field → OData property (only mapped fields are read
            and written).
        read_only: Fields read but never sent (computed or set by SAP).
    """

    version: Version = "v4"
    properties: Mapping[str, str] = field(default_factory=dict)
    read_only: frozenset[str] = frozenset()

    @classmethod
    def for_dataclass(  # type: ignore[override]
        cls,
        model: type[E],
        path: str,
        *,
        version: Version = "v4",
        properties: Mapping[str, str] | None = None,
        read_only: Iterable[str] = (),
        id_field: str = "id",
        service: str = "odata",
    ) -> ODataEndpoint[E]:
        names = [f.name for f in dataclasses.fields(model)]  # type: ignore[arg-type]
        mapping = {n: (properties or {}).get(n, n) for n in names}
        if properties:
            mapping = {n: p for n, p in mapping.items() if n in properties}
        codec = DocumentCodec(model, id_field=id_field)
        reverse = {p: n for n, p in mapping.items()}
        skipped = frozenset(read_only)

        def to_entity(data: Json) -> E:
            values = {reverse[k]: read_value(v) for k, v in data.items() if k in reverse}
            return codec.decode(values)

        def to_payload(entity: E) -> Json:
            document = codec.encode(entity)
            if document.get(id_field) is None:
                document.pop(id_field, None)
            return {
                mapping[n]: write_value(v, version)
                for n, v in document.items()
                if n in mapping and n not in skipped
            }

        compiler = FilterCompiler(version, mapping)

        def list_params(query: ListQuery) -> dict[str, Any]:
            params: dict[str, Any] = {
                "$skip": query.offset,
                "$top": query.limit,
                "$select": ",".join(mapping.values()),
            }
            found = compiler.filter(query)
            if found:
                params["$filter"] = found
            if query.order_by:
                params["$orderby"] = ",".join(
                    f"{compiler.prop(o.lstrip('-'))} {'desc' if o.startswith('-') else 'asc'}"
                    for o in query.order_by
                )
            if version == "v2":
                params |= {"$inlinecount": "allpages", "$format": "json"}
            else:
                params["$count"] = "true"
            return params

        def parse_page(response: httpx.Response, query: ListQuery) -> tuple[list[Json], int]:
            body = response.json()
            if version == "v2":
                data = body.get("d", body)
                items = data.get("results", [])
                total = data.get("__count")
            else:
                items = body.get("value", [])
                total = body.get("@odata.count")
            return items, int(total) if total is not None else query.offset + len(items)

        return cls(
            path=path,
            to_entity=to_entity,
            to_payload=to_payload,
            id_field=id_field,
            update_method="PATCH",
            list_params=list_params,
            parse_page=parse_page,
            service=service,
            version=version,
            properties=mapping,
            read_only=skipped,
        )


# -------------------------------------------------------------- repository

#: CSRF tokens per client (the token goes with the session cookie in its jar).
_TOKENS: weakref.WeakKeyDictionary[httpx.AsyncClient, str] = weakref.WeakKeyDictionary()


class ODataRepository[E](RestRepository[E]):
    """``Repository`` over an OData entity set (see the module docstring)."""

    supports_conditions = True
    supports_versions = False

    def __init__(self, client: httpx.AsyncClient, endpoint: ODataEndpoint[E]) -> None:
        super().__init__(client, endpoint)
        self.odata = endpoint
        self.etags: dict[Any, str] = {}

    def _item(self, entity_id: Any) -> str:
        return f"{self.endpoint.path}({literal(entity_id, self.odata.version)})"

    def _unwrap(self, body: Json) -> Json:
        return body.get("d", body) if self.odata.version == "v2" else body

    def _remember(self, entity_id: Any, body: Json) -> None:
        etag = (body.get("__metadata") or {}).get("etag") or body.get("@odata.etag")
        if etag:
            self.etags[entity_id] = etag

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.odata.version == "v4":
            headers["Content-Type"] = "application/json;IEEE754Compatible=true"
        return {**headers, **(extra or {})}

    async def _token(self, *, fresh: bool = False) -> str:
        if fresh or self.client not in _TOKENS:
            root = self.endpoint.path.rsplit("/", 1)[0] + "/"
            response = await super()._send(
                "csrf", "GET", root, headers={"X-CSRF-Token": "Fetch", "Accept": "application/json"}
            )
            _TOKENS[self.client] = response.headers.get("x-csrf-token", "")
        return _TOKENS[self.client]

    async def _write(self, operation: str, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = self._headers(kw.pop("headers", None))
        for attempt in (0, 1):
            token = await self._token(fresh=attempt == 1)
            if token:
                headers["X-CSRF-Token"] = token
            response = await super()._send(operation, method, url, headers=headers, **kw)
            if not (
                response.status_code == 403
                and response.headers.get("x-csrf-token", "").lower() == "required"
            ):
                return response
        return response

    def _conflict(self, response: httpx.Response, entity_id: Any, operation: str) -> None:
        if response.status_code == 412:
            self.etags.pop(entity_id, None)
            raise VersionConflict(
                f"{self.endpoint.service}: the record {entity_id!r} changed since it was read",
                hint="Reload the record and apply your change again.",
                details={"id": entity_id, "reason": "etag", "operation": operation},
            )

    async def get(self, entity_id: Any) -> E | None:
        params = {"$select": ",".join(self.odata.properties.values())}
        if self.odata.version == "v2":
            params["$format"] = "json"
        response = await self._send(
            "get", "GET", self._item(entity_id), params=params, headers=self._headers()
        )
        if response.status_code == 404:
            return None
        self._check(response, "get")
        body = self._unwrap(response.json())
        self._remember(entity_id, body)
        return self.endpoint.to_entity(body)

    async def list(self, query: ListQuery) -> Page[E]:
        response = await self._send(
            "list",
            "GET",
            self.endpoint.path,
            params=dict(self.endpoint.list_params(query)),
            headers=self._headers(),
        )
        self._check(response, "list")
        items, total = self.endpoint.parse_page(response, query)
        return Page([self.endpoint.to_entity(i) for i in items], total, query.offset, query.limit)

    async def add(self, entity: E) -> E:
        response = await self._write(
            "create", "POST", self.endpoint.path, json=self.endpoint.to_payload(entity)
        )
        self._check(response, "create")
        if response.status_code == 204 or not response.content:
            return entity
        body = self._unwrap(response.json())
        created = self.endpoint.to_entity(body)
        entity_id = getattr(created, self.endpoint.id_field)
        setattr(entity, self.endpoint.id_field, entity_id)
        self._remember(entity_id, body)
        return created

    async def update(self, entity: E) -> E:
        entity_id = getattr(entity, self.endpoint.id_field)
        payload = self.endpoint.to_payload(entity)
        payload.pop(self.odata.properties.get(self.endpoint.id_field, ""), None)
        response = await self._write(
            "update",
            "PATCH",
            self._item(entity_id),
            json=payload,
            headers={"If-Match": self.etags.get(entity_id, "*")},
        )
        self._conflict(response, entity_id, "update")
        self._check(response, "update")
        etag = response.headers.get("etag")
        if etag:
            self.etags[entity_id] = etag
        return entity

    async def delete(self, entity: E) -> None:
        entity_id = getattr(entity, self.endpoint.id_field)
        response = await self._write(
            "delete",
            "DELETE",
            self._item(entity_id),
            headers={"If-Match": self.etags.get(entity_id, "*")},
        )
        self._conflict(response, entity_id, "delete")
        if response.status_code != 404:
            self._check(response, "delete")
        self.etags.pop(entity_id, None)


def odata_unit_of_work_factory(
    client: httpx.AsyncClient, endpoints: Mapping[str, ODataEndpoint[Any]]
) -> Callable[[], RemoteUnitOfWork]:
    """``uow_factory`` serving each resource from its OData entity set."""
    factories = {
        name: (lambda ep=ep: ODataRepository(client, ep)) for name, ep in endpoints.items()
    }
    return lambda: RemoteUnitOfWork(factories)


__all__ = [
    "FilterCompiler",
    "ODataEndpoint",
    "ODataRepository",
    "literal",
    "odata_unit_of_work_factory",
    "read_value",
    "write_value",
]
