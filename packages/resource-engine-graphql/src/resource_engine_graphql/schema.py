"""A GraphQL schema generated from the registry; resolvers call ``ResourceManager``.

For each resource (``invoices``):

- **Type** ``Invoice``: the read schema's fields in camelCase, plus relation
  fields (``customerRecord``; ``tags(offset, limit, orderBy, filter)``),
  ``allowedTransitions`` and ``history``. Every field but ``id`` is nullable,
  because field permissions may hide any of them.
- **Queries:** ``invoice(id)`` and ``invoices(offset, limit, orderBy,
  filter)`` returning ``InvoicePage { items total offset limit }``.
- **Mutations:** ``createInvoice``, ``updateInvoice``, ``deleteInvoice``,
  one per transition (``approveInvoice``) and action
  (``recalculateTotalsInvoice``), with ``idempotencyKey`` and
  ``expectedVersion``.

See docs/graphql.md.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import re
import types
import typing
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from graphql import (
    GraphQLArgument,
    GraphQLBoolean,
    GraphQLField,
    GraphQLFloat,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLScalarType,
    GraphQLSchema,
    GraphQLString,
    StringValueNode,
    value_from_ast_untyped,
)
from pydantic import BaseModel, TypeAdapter
from resource_engine import ListQuery, ResourceDefinition, ResourceManager, ResourceRegistry
from resource_engine.errors import ConfigurationError, PermissionDenied
from resource_engine.messaging.envelope import to_jsonable

DOC = "docs/graphql.md"

# ------------------------------------------------------------------ names


def camel(name: str) -> str:
    head, *rest = [p for p in name.split("_") if p] or [name]
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_") if p)


def singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith(("ses", "xes")):
        return name[:-2]
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


# ---------------------------------------------------------------- scalars


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"Not a decimal: {value!r}") from None


def _literal_string(node: Any, _variables: Any = None) -> Any:
    if isinstance(node, StringValueNode):
        return node.value
    return value_from_ast_untyped(node)


DecimalScalar = GraphQLScalarType(
    "Decimal",
    serialize=lambda v: str(v),
    parse_value=_decimal,
    parse_literal=lambda node, _v=None: _decimal(value_from_ast_untyped(node)),
    description='A decimal number as a string ("120.50"), never a float.',
)
DateScalar = GraphQLScalarType(
    "Date",
    serialize=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
    parse_value=str,
    parse_literal=_literal_string,
    description="An ISO 8601 date (2026-11-25).",
)
DateTimeScalar = GraphQLScalarType(
    "DateTime",
    serialize=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
    parse_value=str,
    parse_literal=_literal_string,
    description="An ISO 8601 timestamp with a time zone.",
)
UUIDScalar = GraphQLScalarType(
    "UUID", serialize=str, parse_value=str, parse_literal=_literal_string
)
JSONScalar = GraphQLScalarType(
    "JSON",
    serialize=to_jsonable,
    parse_value=lambda v: v,
    parse_literal=lambda node, _v=None: value_from_ast_untyped(node),
    description="Any JSON value.",
)

_SCALARS: dict[Any, Any] = {
    str: GraphQLString,
    bool: GraphQLBoolean,
    int: GraphQLInt,
    float: GraphQLFloat,
    Decimal: DecimalScalar,
    dt.date: DateScalar,
    dt.datetime: DateTimeScalar,
    dt.time: GraphQLString,
    uuid.UUID: UUIDScalar,
}


# ------------------------------------------------------------- resolvers


def _value(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    try:
        return getattr(obj, name)
    except AttributeError:  # a field hidden by field permissions
        return None


def _attribute(name: str) -> Callable[..., Any]:
    return lambda obj, _info, **_kw: _value(obj, name)


# ------------------------------------------------------------------ builder


@dataclasses.dataclass
class _Resource:
    definition: ResourceDefinition
    type: GraphQLObjectType
    single: str
    plural: str
    fields: dict[str, str]  # camelCase → snake_case of the read schema


class SchemaBuilder:
    """Builds the schema once; keeps the name maps resolvers need."""

    def __init__(
        self, registry: ResourceRegistry, manager: ResourceManager, *, hub: Any = None
    ) -> None:
        self.registry = registry
        self.manager = manager
        self.hub = hub
        self.types: dict[str, Any] = {}
        self.models: dict[tuple[type, bool], Any] = {}
        self.resources: dict[str, _Resource] = {}
        self.pages: dict[str, GraphQLObjectType] = {}
        self.filters: dict[tuple[str, bool], GraphQLInputObjectType] = {}

    # ----------------------------------------------------------- types

    def _name(self, name: str) -> str:
        if name in self.types:
            raise ConfigurationError(
                f"Two GraphQL types are named {name!r}",
                hint="Rename one of the schemas or resources.",
                doc=DOC,
            )
        return name

    def graphql_type(self, annotation: Any, *, input: bool, hint: str) -> Any:
        """A GraphQL type for a Python annotation (nullable; callers wrap NonNull)."""
        origin = typing.get_origin(annotation)
        if origin is typing.Annotated:
            return self.graphql_type(typing.get_args(annotation)[0], input=input, hint=hint)
        if origin in (typing.Union, types.UnionType):
            options = [a for a in typing.get_args(annotation) if a is not type(None)]
            if len(options) == 1:
                return self.graphql_type(options[0], input=input, hint=hint)
            return JSONScalar
        if origin is typing.Literal:
            values = typing.get_args(annotation)
            if all(isinstance(v, bool) for v in values):
                return GraphQLBoolean
            if all(isinstance(v, int) for v in values):
                return GraphQLInt
            return GraphQLString
        if origin in (list, tuple, set, frozenset, typing.Sequence):
            args = [a for a in typing.get_args(annotation) if a is not Ellipsis]
            inner = self.graphql_type(args[0], input=input, hint=hint) if args else JSONScalar
            return GraphQLList(inner)
        if origin in (dict, Mapping) or annotation in (dict, Any, object):
            return JSONScalar
        if isinstance(annotation, type):
            if issubclass(annotation, bool):
                return GraphQLBoolean
            if issubclass(annotation, enum.Enum):
                return GraphQLString
            for python, gql in _SCALARS.items():
                if annotation is python or (
                    python is not dt.date and issubclass(annotation, python)
                ):
                    return gql
            if issubclass(annotation, dt.datetime):
                return DateTimeScalar
            if issubclass(annotation, dt.date):
                return DateScalar
            if issubclass(annotation, BaseModel):
                return self.model_type(annotation, input=input)
        return JSONScalar

    def model_type(self, model: type[BaseModel], *, input: bool) -> Any:
        """An object (or input object) type for a nested Pydantic model."""
        key = (model, input)
        if key in self.models:
            return self.models[key]
        name = self._name(f"{model.__name__}Input" if input else model.__name__)
        if input:
            gql: Any = GraphQLInputObjectType(
                name,
                lambda: self.input_fields(model),
                description=_doc(model),
            )
        else:
            gql = GraphQLObjectType(
                name,
                lambda: {
                    camel(f): GraphQLField(
                        self.graphql_type(info.annotation, input=False, hint=f),
                        resolve=_attribute(f),
                        description=info.description,
                    )
                    for f, info in model.model_fields.items()
                },
                description=_doc(model),
            )
        self.types[name] = gql
        self.models[key] = gql
        return gql

    def input_fields(self, model: type[BaseModel]) -> dict[str, GraphQLInputField]:
        out = {}
        for name, info in model.model_fields.items():
            gql = self.graphql_type(info.annotation, input=True, hint=name)
            if info.is_required():
                gql = GraphQLNonNull(gql)
            out[camel(name)] = GraphQLInputField(gql, out_name=name, description=info.description)
        return out

    def input_type(self, model: type[BaseModel] | None) -> Any:
        return None if model is None else self.model_type(model, input=True)

    # ------------------------------------------------------- resources

    def build(self) -> GraphQLSchema:
        for d in self.registry:
            if not (d.exposed("get") or d.exposed("list")) or "read" not in d.schemas:
                continue
            name = self._name(pascal(singular(d.name)))
            read = d.schemas["read"]
            fields = {camel(f): f for f in read.model_fields}
            plural = camel(d.name)
            single = camel(singular(d.name))
            if single == plural:
                single = f"{single}ById"
            gql = GraphQLObjectType(name, self._thunk(d), description=_doc(d.resource_cls))
            self.types[name] = gql
            self.resources[d.name] = _Resource(d, gql, single, plural, fields)
        query = GraphQLObjectType("Query", self._queries)
        mutations = self._mutations()
        subscriptions = self._subscriptions() if self.hub is not None else {}
        return GraphQLSchema(
            query=query,
            mutation=GraphQLObjectType("Mutation", mutations) if mutations else None,
            subscription=GraphQLObjectType("Subscription", subscriptions)
            if subscriptions
            else None,
            types=[DecimalScalar, DateScalar, DateTimeScalar, JSONScalar],
        )

    # ---------------------------------------------------- subscriptions

    def _subscriptions(self) -> dict[str, GraphQLField]:
        out: dict[str, GraphQLField] = {}
        for name, r in self.resources.items():
            d = r.definition
            if not d.exposed("get"):
                continue
            id_type = self.graphql_type(d.id_type, input=True, hint="id")
            change = self._name(f"{r.type.name}Change")
            change_type = GraphQLObjectType(
                change,
                lambda r=r, id_type=id_type: {
                    "event": GraphQLField(
                        GraphQLNonNull(GraphQLString),
                        resolve=_attribute("event"),
                        description="ResourceCreated, ResourceUpdated, TransitionExecuted, "
                        "ResourceDeleted, a document or domain event...",
                    ),
                    "id": GraphQLField(GraphQLNonNull(id_type), resolve=_attribute("id")),
                    "occurredAt": GraphQLField(
                        GraphQLNonNull(DateTimeScalar), resolve=_attribute("occurred_at")
                    ),
                    "record": GraphQLField(
                        r.type,
                        resolve=_attribute("record"),
                        description="The record as the subscriber reads it now; null once deleted",
                    ),
                },
                description=f"A committed change to a {name} record",
            )
            self.types[change] = change_type
            key = f"{camel(singular(name))}Changed"
            out[key] = GraphQLField(
                GraphQLNonNull(change_type),
                args={
                    "id": GraphQLArgument(id_type, description="Only this record"),
                    "events": GraphQLArgument(
                        GraphQLList(GraphQLNonNull(GraphQLString)),
                        description="Only these event types",
                    ),
                    **({"tenant": GraphQLArgument(GraphQLString)} if d.tenant_field else {}),
                },
                subscribe=self._subscribe(name),
                resolve=lambda change, _info, **_kw: change,
                description=f"Committed changes to {name} records the subscriber can read",
            )
        return out

    def _subscribe(self, resource: str) -> Callable[..., Any]:
        from resource_engine.errors import (
            EntityNotFound,
            PermissionDenied,
            TenantMismatch,
            TenantRequired,
        )

        d = self.resources[resource].definition
        adapter = TypeAdapter(d.id_type)
        hidden = (EntityNotFound, PermissionDenied, TenantRequired, TenantMismatch)

        def entity_of(envelope: Any) -> Any:
            payload = envelope.payload
            if (payload.get("resource") or envelope.resource) != resource:
                return None
            value = payload.get("entity_id")
            if value is None:
                return None
            try:
                return adapter.validate_python(value)
            except Exception:
                return None

        async def subscribe(_root: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            manager, actor, tenant = c.manager, c.actor, kw.get("tenant")
            only = self._id(resource, kw["id"]) if kw.get("id") is not None else None
            events = set(kw.get("events") or ())
            if only is not None:
                await manager.get(resource, only, actor=actor, tenant=tenant)  # visible now?
            elif not await manager.can(actor, resource, "get", tenant=tenant):
                raise PermissionDenied(
                    f"Not allowed to read {resource}", details={"action": f"{resource}:get"}
                )
            # checks above raise before the stream starts: an ``error`` message
            return stream(manager, actor, tenant, only, events)

        async def stream(
            manager: Any, actor: Any, tenant: Any, only: Any, events: set[str]
        ) -> AsyncIterator[dict[str, Any]]:
            subscription = self.hub.subscribe(
                lambda env: (
                    (entity := entity_of(env)) is not None
                    and (only is None or entity == only)
                    and (not events or env.type in events)
                )
            )
            delivered: set[Any] = {only} if only is not None else set()
            try:
                async for envelope in subscription:
                    entity_id = entity_of(envelope)
                    change = {
                        "event": envelope.type,
                        "id": entity_id,
                        "occurred_at": envelope.occurred_at,
                        "record": None,
                    }
                    if envelope.type == "ResourceDeleted":
                        if entity_id in delivered:  # never reveal unseen records' ids
                            delivered.discard(entity_id)
                            yield change
                        continue
                    try:
                        change["record"] = await manager.get(
                            resource, entity_id, actor=actor, tenant=tenant
                        )
                    except hidden:
                        continue  # not (or no longer) visible to this subscriber
                    delivered.add(entity_id)
                    yield change
            finally:
                subscription.close()

        return subscribe

    def _thunk(self, d: ResourceDefinition) -> Callable[[], dict[str, GraphQLField]]:
        def fields() -> dict[str, GraphQLField]:
            read = d.schemas["read"]
            out: dict[str, GraphQLField] = {}
            for f, info in read.model_fields.items():
                gql = self.graphql_type(info.annotation, input=False, hint=f)
                if f == d.id_field:
                    gql = GraphQLNonNull(gql)
                out[camel(f)] = GraphQLField(
                    gql, resolve=_attribute(f), description=info.description
                )
            for ref in d.references.values():
                if ref.target not in self.resources:
                    continue
                out[self._field_name(out, ref.name, d)] = GraphQLField(
                    self.resources[ref.target].type,
                    resolve=self._forward(ref),
                    description=ref.description
                    or f"The {ref.target} record of {ref.field} (null when unreadable)",
                )
            for relation, rel in self.manager.reverse.get(d.name, {}).items():
                if rel.owner not in self.resources:
                    continue
                out[self._field_name(out, relation, d)] = GraphQLField(
                    self.page_type(rel.owner),
                    args=self.list_args(rel.owner, skip=rel.field),
                    resolve=self._reverse(d, relation),
                    description=(
                        f"The {rel.owner} records whose {rel.field} is this record"
                        if rel.field
                        else f"The {rel.owner} linked through {rel.join}"
                    )
                    + " (null when they cannot be listed)",
                )
            if d.machine is not None and d.expose_transitions:
                out[self._field_name(out, "allowed_transitions", d)] = GraphQLField(
                    GraphQLList(GraphQLNonNull(GraphQLString)),
                    resolve=self._allowed(d),
                    description="Transitions the caller may run on the record now",
                )
                if self.manager.has_history and d.resource_cls.audit:
                    out[self._field_name(out, "history", d)] = GraphQLField(
                        GraphQLList(GraphQLNonNull(self.history_type())),
                        args=(
                            {
                                "machine": GraphQLArgument(
                                    GraphQLString, description="Only this state machine"
                                )
                            }
                            if len(d.machines) > 1
                            else {}
                        ),
                        resolve=self._history(d),
                        description="Creation and committed transitions, oldest first",
                    )
            return out

        return fields

    def _field_name(self, existing: Mapping[str, Any], name: str, d: ResourceDefinition) -> str:
        key = camel(name)
        if key in existing:
            raise ConfigurationError(
                f"GraphQL field {key!r} of {d.name} is both a field and a relation",
                hint="Rename the relation (Reference name= / related_name=).",
                doc=DOC,
            )
        return key

    def history_type(self) -> Any:
        from resource_engine.audit.history import StateChange

        return self.model_type(StateChange, input=False)

    def page_type(self, resource: str) -> GraphQLObjectType:
        if resource not in self.pages:
            r = self.resources[resource]
            name = self._name(f"{r.type.name}Page")
            self.pages[resource] = GraphQLObjectType(
                name,
                lambda: {
                    "items": GraphQLField(
                        GraphQLNonNull(GraphQLList(GraphQLNonNull(r.type))),
                        resolve=_attribute("items"),
                    ),
                    "total": GraphQLField(GraphQLNonNull(GraphQLInt), resolve=_attribute("total")),
                    "offset": GraphQLField(
                        GraphQLNonNull(GraphQLInt), resolve=_attribute("offset")
                    ),
                    "limit": GraphQLField(GraphQLNonNull(GraphQLInt), resolve=_attribute("limit")),
                },
                description=f"A page of {resource}: items, total (every match), offset, limit",
            )
            self.types[name] = self.pages[resource]
        return self.pages[resource]

    def filter_type(self, resource: str, *, related: bool = True) -> GraphQLInputObjectType | None:
        """``<Type>Filter``: equality on the filterable fields, and (with
        ``related``) ``<relation>: <Target>FieldFilter`` for related records."""
        key = (resource, related)
        if key in self.filters:
            return self.filters[key]
        r = self.resources[resource]
        d = r.definition
        read = d.schemas["read"]
        fields: dict[str, GraphQLInputField] = {}
        for f in d.filterable:
            annotation = read.model_fields[f].annotation if f in read.model_fields else str
            fields[camel(f)] = GraphQLInputField(
                self.graphql_type(annotation, input=True, hint=f), out_name=f
            )
        if related:
            for ref in d.references.values():
                if ref.target in self.resources:
                    inner = self.filter_type(ref.target, related=False)
                    if inner is not None:
                        fields[camel(ref.name)] = GraphQLInputField(inner, out_name=ref.name)
        if not fields:
            return None
        name = self._name(f"{r.type.name}{'Filter' if related else 'FieldFilter'}")
        gql = GraphQLInputObjectType(name, fields)
        self.types[name] = gql
        self.filters[key] = gql
        return gql

    def list_args(self, resource: str, *, skip: str | None = None) -> dict[str, GraphQLArgument]:
        d = self.resources[resource].definition
        args = {
            "offset": GraphQLArgument(GraphQLInt, default_value=0),
            "limit": GraphQLArgument(GraphQLInt, default_value=50),
            "orderBy": GraphQLArgument(
                GraphQLList(GraphQLNonNull(GraphQLString)),
                out_name="order_by",
                description=f"Fields to sort by ({', '.join(camel(f) for f in d.sortable)}); "
                "prefix '-' for descending",
            ),
        }
        filter_type = self.filter_type(resource)
        if filter_type is not None:
            args["filter"] = GraphQLArgument(filter_type)
        if d.tenant_field:
            args["tenant"] = GraphQLArgument(GraphQLString)
        return args

    # ---------------------------------------------------------- queries

    def _queries(self) -> dict[str, GraphQLField]:
        out: dict[str, GraphQLField] = {}
        for name, r in self.resources.items():
            d = r.definition
            id_type = GraphQLNonNull(self.graphql_type(d.id_type, input=True, hint="id"))
            tenant = {"tenant": GraphQLArgument(GraphQLString)} if d.tenant_field else {}
            if d.exposed("get"):
                out[r.single] = GraphQLField(
                    r.type,
                    args={"id": GraphQLArgument(id_type), **tenant},
                    resolve=self._get(name),
                    description=d.operation_docs.get("get") or f"One {name} record",
                )
            if d.exposed("list"):
                out[r.plural] = GraphQLField(
                    GraphQLNonNull(self.page_type(name)),
                    args=self.list_args(name),
                    resolve=self._list(name),
                    description=d.operation_docs.get("list") or f"A page of {name}",
                )
        return out

    # -------------------------------------------------------- mutations

    def _mutations(self) -> dict[str, GraphQLField]:
        out: dict[str, GraphQLField] = {}

        def add(name: str, field: GraphQLField, resource: str) -> None:
            if name in out:
                raise ConfigurationError(
                    f"Two GraphQL mutations are named {name!r} ({resource})",
                    hint="Rename the transition or action.",
                    doc=DOC,
                )
            out[name] = field

        for name, r in self.resources.items():
            d = r.definition
            type_name = r.type.name
            id_arg = {
                "id": GraphQLArgument(
                    GraphQLNonNull(self.graphql_type(d.id_type, input=True, hint="id"))
                )
            }
            common = {"idempotencyKey": GraphQLArgument(GraphQLString, out_name="idempotency_key")}
            if d.tenant_field:
                common["tenant"] = GraphQLArgument(GraphQLString)
            version = (
                {"expectedVersion": GraphQLArgument(GraphQLInt, out_name="expected_version")}
                if d.version_field
                else {}
            )
            if d.exposed("create"):
                add(
                    f"create{type_name}",
                    GraphQLField(
                        r.type,
                        args={
                            "data": GraphQLArgument(
                                GraphQLNonNull(self.input_type(d.schemas["create"]))
                            ),
                            **common,
                        },
                        resolve=self._write(name, "create"),
                        description=d.operation_docs.get("create"),
                    ),
                    name,
                )
            if d.exposed("update"):
                add(
                    f"update{type_name}",
                    GraphQLField(
                        r.type,
                        args={
                            **id_arg,
                            "data": GraphQLArgument(
                                GraphQLNonNull(self.input_type(d.schemas["update"]))
                            ),
                            **version,
                            **common,
                        },
                        resolve=self._write(name, "update"),
                        description=d.operation_docs.get("update"),
                    ),
                    name,
                )
            if d.exposed("delete"):
                add(
                    f"delete{type_name}",
                    GraphQLField(
                        GraphQLBoolean,
                        args={**id_arg, **version, **common},
                        resolve=self._write(name, "delete"),
                        description=d.operation_docs.get("delete"),
                    ),
                    name,
                )
            if d.machine is not None and d.expose_transitions:
                for event in d.transitions:
                    schema = d.transition_schemas.get(event)
                    data = (
                        {"data": GraphQLArgument(GraphQLNonNull(self.input_type(schema)))}
                        if schema
                        else {}
                    )
                    add(
                        f"{camel(event)}{type_name}",
                        GraphQLField(
                            r.type,
                            args={**id_arg, **data, **version, **common},
                            resolve=self._write(name, "transition", event),
                            description=f"Run the {event} transition",
                        ),
                        name,
                    )
            for action in d.actions.values():
                data = (
                    {"data": GraphQLArgument(GraphQLNonNull(self.input_type(action.input_schema)))}
                    if action.input_schema
                    else {}
                )
                if action.output_schema is not None:
                    result: Any = self.graphql_type(action.output_schema, input=False, hint="out")
                elif action.instance:
                    result = r.type
                else:
                    result = JSONScalar
                add(
                    f"{camel(action.name)}{type_name}",
                    GraphQLField(
                        result,
                        args={
                            **(id_arg if action.instance else {}),
                            **data,
                            **(version if action.instance else {}),
                            **common,
                        },
                        resolve=self._write(name, "action", action.name),
                        description=_first_line(action.description),
                    ),
                    name,
                )
        return out

    # -------------------------------------------------------- resolvers

    @staticmethod
    def _ctx(info: Any) -> Any:
        return info.context

    def _query(self, resource: str, kw: Mapping[str, Any]) -> ListQuery:
        r = self.resources[resource]
        order = []
        for item in kw.get("order_by") or ():
            desc = item.startswith("-")
            field = r.fields.get(item.lstrip("-"), item.lstrip("-"))
            order.append(f"-{field}" if desc else field)
        filters: dict[str, Any] = {}
        for key, value in (kw.get("filter") or {}).items():
            if isinstance(value, Mapping):  # a related record's fields
                filters.update({f"{key}.{f}": v for f, v in value.items() if v is not None})
            elif value is not None:
                filters[key] = value
        return ListQuery(
            offset=kw.get("offset") or 0,
            limit=kw.get("limit") or 50,
            order_by=order,
            filters=filters,
        )

    def _id(self, resource: str, value: Any) -> Any:
        return TypeAdapter(self.resources[resource].definition.id_type).validate_python(value)

    def _get(self, resource: str) -> Callable[..., Any]:
        async def resolve(_root: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            return await c.manager.get(
                resource, self._id(resource, kw["id"]), actor=c.actor, tenant=kw.get("tenant")
            )

        return resolve

    def _list(self, resource: str) -> Callable[..., Any]:
        async def resolve(_root: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            return await c.manager.list(
                resource, self._query(resource, kw), actor=c.actor, tenant=kw.get("tenant")
            )

        return resolve

    def _forward(self, ref: Any) -> Callable[..., Any]:
        async def resolve(obj: Any, info: Any, **_kw: Any) -> Any:
            value = _value(obj, ref.field)
            if value is None:
                return None
            return await self._ctx(info).loader(ref.target).load(value)

        return resolve

    def _reverse(self, d: ResourceDefinition, relation: str) -> Callable[..., Any]:
        rel = self.manager.reverse[d.name][relation]

        async def resolve(obj: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            parent = _value(obj, d.id_field)
            if parent is None:
                return None
            try:
                return await c.manager.list_related(
                    d.name,
                    parent,
                    relation,
                    self._query(rel.owner, kw),
                    actor=c.actor,
                    tenant=kw.get("tenant"),
                )
            except PermissionDenied:
                return None

        return resolve

    def _allowed(self, d: ResourceDefinition) -> Callable[..., Any]:
        async def resolve(obj: Any, info: Any, **_kw: Any) -> Any:
            c = self._ctx(info)
            return await c.manager.allowed_transitions(
                d.name, _value(obj, d.id_field), actor=c.actor
            )

        return resolve

    def _history(self, d: ResourceDefinition) -> Callable[..., Any]:
        async def resolve(obj: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            return await c.manager.history(
                d.name, _value(obj, d.id_field), actor=c.actor, machine=kw.get("machine")
            )

        return resolve

    def _write(
        self, resource: str, operation: str, target: str | None = None
    ) -> Callable[..., Any]:
        async def resolve(_root: Any, info: Any, **kw: Any) -> Any:
            c = self._ctx(info)
            m = c.manager
            options: dict[str, Any] = {
                "actor": c.actor,
                "tenant": kw.get("tenant"),
                "idempotency_key": kw.get("idempotency_key"),
            }
            entity_id = self._id(resource, kw["id"]) if "id" in kw else None
            version = kw.get("expected_version")
            data = kw.get("data")
            if operation == "create":
                return await m.create(resource, data, **options)
            if operation == "update":
                return await m.update(
                    resource, entity_id, data, expected_version=version, **options
                )
            if operation == "delete":
                await m.delete(resource, entity_id, expected_version=version, **options)
                return True
            if operation == "transition":
                return await m.transition(
                    resource, entity_id, target, data, expected_version=version, **options
                )
            return await m.run_action(
                resource, target, entity_id, data, expected_version=version, **options
            )

        return resolve


def _doc(obj: Any) -> str | None:
    text = obj.__doc__
    if not text or text.startswith(f"{getattr(obj, '__name__', '')}("):
        return None
    return _first_line(text)


def _first_line(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    return re.split(r"\n\s*\n", text.strip())[0].replace("\n", " ").strip()
