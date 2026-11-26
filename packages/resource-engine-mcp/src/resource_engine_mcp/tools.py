"""MCP tools generated from the registry: one tool per operation.

Pure description: names, input and output JSON Schemas, annotations and
descriptions. Calling a tool goes through ``ResourceManager`` (see ``server``).
"""

from __future__ import annotations

import fnmatch
import inspect
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

import mcp_types as types
from pydantic import BaseModel, TypeAdapter
from resource_engine import ResourceDefinition, ResourceManager, ResourceRegistry
from resource_engine.errors import ConfigurationError

#: MCP tool names: letters, digits, ``_``, ``-`` and ``.``, at most 128 characters.
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

READ_OPERATIONS = frozenset({"list", "get", "allowed_transitions", "history", "related"})


@dataclass(frozen=True)
class ResourceTool:
    """One MCP tool and the manager call behind it.

    Attributes:
        name: Tool name (``invoices_list``, ``invoices_transition_approve``).
        resource: Resource name.
        operation: ``list``, ``get``, ``create``, ``update``, ``delete``,
            ``allowed_transitions``, ``history``, ``transition`` or ``action``.
        permission: The action checked by the authorizer, without the resource
            prefix (``update``, ``transition:approve``, ``action:recalculate``).
        target: The transition event or action name.
        instance: Whether the tool acts on one record (takes ``id``).
    """

    name: str
    resource: str
    operation: str
    permission: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    target: str | None = None
    instance: bool = True
    destructive: bool = False
    wraps_result: bool = False
    #: The resource whose permission gates the tool (default ``resource``).
    permission_resource: str | None = None

    @property
    def read_only(self) -> bool:
        return self.operation in READ_OPERATIONS

    def to_mcp(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            annotations=types.ToolAnnotations(
                title=self.title,
                read_only_hint=self.read_only,
                destructive_hint=None if self.read_only else self.destructive,
                idempotent_hint=True if self.read_only else None,
                open_world_hint=False,
            ),
        )


# ----------------------------------------------------------------- schemas


class _Schema:
    """Builds one tool's input schema; model definitions go to the root ``$defs``."""

    def __init__(self) -> None:
        self.properties: dict[str, Any] = {}
        self.required: list[str] = []
        self.defs: dict[str, Any] = {}

    def model(self, model: type[BaseModel], *, mode: str = "validation") -> dict[str, Any]:
        schema = model.model_json_schema(
            by_alias=True,
            ref_template="#/$defs/{model}",
            mode=mode,  # type: ignore[arg-type]
        )
        self.defs.update(schema.pop("$defs", {}))
        return schema

    def type(self, annotation: Any) -> dict[str, Any]:
        schema = TypeAdapter(annotation).json_schema(ref_template="#/$defs/{model}")
        self.defs.update(schema.pop("$defs", {}))
        return schema

    def add(self, name: str, schema: dict[str, Any], *, required: bool = False) -> None:
        self.properties[name] = schema
        if required:
            self.required.append(name)

    def build(self) -> dict[str, Any]:
        root: dict[str, Any] = {
            "type": "object",
            "properties": self.properties,
            "additionalProperties": False,
        }
        if self.required:
            root["required"] = self.required
        if self.defs:
            root["$defs"] = self.defs
        return root


def _output(model: type[BaseModel] | None) -> tuple[dict[str, Any] | None, bool]:
    """A read model's schema as a tool output schema, and whether results are
    wrapped in ``{"result": ...}`` (non-object schemas). Fields are not
    required: field-level permissions may hide some of them."""
    if model is None:
        return None, True
    schema = _relaxed(model.model_json_schema(by_alias=True, mode="serialization"))
    if schema.get("type") != "object":
        return {"type": "object", "properties": {"result": schema}}, True
    return schema, False


def _relaxed(schema: dict[str, Any]) -> dict[str, Any]:
    schema = dict(schema)
    schema.pop("required", None)
    return schema


def _page_output(read: type[BaseModel] | None, relations: Iterable[str] = ()) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "object"}
    defs: dict[str, Any] = {}
    if read is not None:
        item = _relaxed(read.model_json_schema(by_alias=True, mode="serialization"))
        defs = item.pop("$defs", {})
    for name in relations:
        item.setdefault("properties", {})[name] = {"type": ["object", "null"]}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": item},
            "total": {"type": "integer"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["items", "total", "offset", "limit"],
    }
    if defs:
        schema["$defs"] = defs
    return schema


# ------------------------------------------------------------- descriptions


def _first_paragraph(text: str | None) -> str:
    if not text or not text.strip():
        return ""
    return inspect.cleandoc(text).split("\n\n")[0].replace("\n", " ").strip()


def _describe(definition: ResourceDefinition, text: str, extra: Iterable[str] = ()) -> str:
    about = _first_paragraph(inspect.getdoc(definition.resource_cls))
    parts = [text.strip()]
    if about:
        parts.append(f"Resource {definition.name}: {about}")
    parts.extend(e for e in extra if e)
    return "\n\n".join(p for p in parts if p)


def _state_names(definition: ResourceDefinition) -> dict[str, str]:
    info = definition.machine.info  # type: ignore[union-attr]
    return {s.id: str(s.value if s.value is not None else s.id) for s in info.states}


# ------------------------------------------------------------------- build


class _Builder:
    def __init__(self, definition: ResourceDefinition, manager: ResourceManager) -> None:
        self.d = definition
        self.manager = manager
        self.docs = definition.operation_docs
        self.read = definition.schemas.get("read")

    def _id(self, schema: _Schema) -> None:
        id_schema = schema.type(self.d.id_type)
        id_schema.setdefault("description", f"Id of the {self.d.name} record")
        schema.add("id", id_schema, required=True)

    def _expand(self, schema: _Schema, definition: ResourceDefinition | None = None) -> None:
        d = definition or self.d
        reverse = self.manager.reverse.get(d.name, {})
        if not d.references and not reverse:
            return
        names = [re.escape(n) for n in d.references]
        names += [f"{re.escape(n)}(:[0-9]+)?" for n in reverse]
        parts = [f"{r.name} ({r.target} via {r.field})" for r in d.references.values()]
        parts += [
            f"{n} (a page of {r.owner} "
            + (f"whose {r.field} is this record" if r.field else f"linked through {r.join}")
            + f"; '{n}:25' sets the size)"
            for n, r in reverse.items()
        ]
        segment = r"[a-z_][a-z0-9_]*(:[0-9]+)?"
        pattern = "^(" + "|".join(names) + r")(\." + segment + "){0,2}$"
        schema.add(
            "expand",
            {
                "type": "array",
                "items": {"type": "string", "pattern": pattern},
                "description": "Related records to include: "
                + "; ".join(parts)
                + ". Nest with dots (invoices.customer_record), up to 3 levels.",
            },
        )

    def _tail(self, schema: _Schema, *, write: bool, versioned: bool = False) -> None:
        if self.d.tenant_field:
            schema.add(
                "tenant",
                {
                    "type": "string",
                    "description": "Tenant to act on (only for cross-tenant callers)",
                },
            )
        if versioned and self.d.version_field:
            schema.add(
                "expected_version",
                {
                    "type": "integer",
                    "description": f"The record's {self.d.version_field} as last read; "
                    "the write is refused if the record changed since",
                },
            )
        if write:
            schema.add(
                "idempotency_key",
                {
                    "type": "string",
                    "description": "Retry-safe key: repeating the same call with the same key "
                    "runs it once and returns the first result",
                },
            )

    def _data(self, schema: _Schema, model: type[BaseModel] | None, what: str) -> None:
        if model is None:
            return
        data = schema.model(model)
        data.setdefault("description", what)
        schema.add("data", data, required=True)

    def tool(self, suffix: str, **kw: Any) -> ResourceTool:
        name = f"{self.d.name}_{suffix}"
        if not TOOL_NAME.match(name):
            raise ConfigurationError(
                f"MCP tool name {name!r} is not valid",
                hint="Tool names may use letters, digits, '_', '-' and '.', up to 128 "
                "characters. Rename the resource, transition or action, or exclude the tool.",
                doc="docs/mcp.md#tools",
            )
        return ResourceTool(name=name, resource=self.d.name, **kw)

    def _with_relations(self, schema: dict[str, Any] | None) -> dict[str, Any] | None:
        reverse = self.manager.reverse.get(self.d.name, {})
        if schema is None or not (self.d.references or reverse):
            return schema
        schema = {**schema, "properties": dict(schema.get("properties", {}))}
        for ref in self.d.references.values():
            schema["properties"][ref.name] = {
                "type": ["object", "null"],
                "description": f"The {ref.target} record (with expand), null when unreadable",
            }
        for relation, ref in reverse.items():
            schema["properties"][relation] = {
                "type": ["object", "null"],
                "description": f"A page of {ref.owner} (with expand): items, total, limit, "
                "has_more; null when they cannot be listed",
            }
        return schema

    def build(self) -> list[ResourceTool]:
        d = self.d
        tools: list[ResourceTool] = []
        read_output, _ = _output(self.read)
        expanded_output = self._with_relations(read_output)

        if d.enabled("list"):
            schema = _Schema()
            schema.add("offset", {"type": "integer", "minimum": 0, "default": 0})
            schema.add(
                "limit",
                {"type": "integer", "minimum": 1, "maximum": d.max_page_size, "default": 50},
            )
            if d.sortable:
                order = [f for name in d.sortable for f in (name, f"-{name}")]
                schema.add(
                    "order_by",
                    {
                        "type": "array",
                        "items": {"type": "string", "enum": order},
                        "description": "Fields to sort by; prefix '-' for descending",
                    },
                )
            if d.filterable or d.references:
                filters = _Schema()
                for field_name in d.filterable:
                    annotation = self.read.model_fields[field_name].annotation if self.read else str
                    filters.add(field_name, filters.type(annotation))
                for ref in d.references.values():
                    target = self.manager.registry.get(ref.target)
                    target_read = target.schemas.get("read")
                    for field_name in target.filterable:
                        annotation = (
                            target_read.model_fields[field_name].annotation if target_read else str
                        )
                        related = filters.type(annotation)
                        related.setdefault(
                            "description", f"The related {ref.target} record's {field_name}"
                        )
                        filters.add(f"{ref.name}.{field_name}", related)
                schema.defs.update(filters.defs)
                schema.add(
                    "filters",
                    {
                        "type": "object",
                        "properties": filters.properties,
                        "additionalProperties": False,
                        "description": "Equality filters; '<relation>.<field>' filters "
                        "by the related record",
                    },
                )
            self._expand(schema)
            self._tail(schema, write=False)
            tools.append(
                self.tool(
                    "list",
                    operation="list",
                    permission="list",
                    title=f"List {d.name}",
                    description=_describe(d, self.docs.get("list") or f"List {d.name}, paged."),
                    input_schema=schema.build(),
                    output_schema=_page_output(self.read, list(d.references)),
                    instance=False,
                )
            )

        if d.enabled("get"):
            schema = _Schema()
            self._id(schema)
            self._expand(schema)
            self._tail(schema, write=False)
            tools.append(
                self.tool(
                    "get",
                    operation="get",
                    permission="get",
                    title=f"Get one of {d.name}",
                    description=_describe(d, self.docs.get("get") or f"Read one {d.name} record."),
                    input_schema=schema.build(),
                    output_schema=expanded_output,
                )
            )

        if d.enabled("create"):
            schema = _Schema()
            self._data(schema, d.schemas["create"], f"The new {d.name} record")
            self._tail(schema, write=True)
            tools.append(
                self.tool(
                    "create",
                    operation="create",
                    permission="create",
                    title=f"Create {d.name}",
                    description=_describe(
                        d, self.docs.get("create") or f"Create a {d.name} record."
                    ),
                    input_schema=schema.build(),
                    output_schema=read_output,
                    instance=False,
                )
            )

        if d.enabled("update"):
            schema = _Schema()
            self._id(schema)
            self._data(schema, d.schemas["update"], "Fields to change; others are kept")
            self._tail(schema, write=True, versioned=True)
            tools.append(
                self.tool(
                    "update",
                    operation="update",
                    permission="update",
                    title=f"Update {d.name}",
                    description=_describe(
                        d, self.docs.get("update") or f"Change fields of a {d.name} record."
                    ),
                    input_schema=schema.build(),
                    output_schema=read_output,
                )
            )

        if d.enabled("delete"):
            schema = _Schema()
            self._id(schema)
            self._tail(schema, write=True, versioned=True)
            tools.append(
                self.tool(
                    "delete",
                    operation="delete",
                    permission="delete",
                    title=f"Delete {d.name}",
                    description=_describe(
                        d, self.docs.get("delete") or f"Delete a {d.name} record."
                    ),
                    input_schema=schema.build(),
                    output_schema={
                        "type": "object",
                        "properties": {"id": {}, "deleted": {"type": "boolean"}},
                        "required": ["id", "deleted"],
                    },
                    destructive=True,
                )
            )

        for relation, ref in self.manager.reverse.get(d.name, {}).items():
            tools.append(self._related_tool(relation, ref))

        if d.machine is not None and d.expose_transitions:
            tools.extend(self._machine_tools(read_output))

        for action in d.actions.values():
            schema = _Schema()
            if action.instance:
                self._id(schema)
            self._data(schema, action.input_schema, "Action input")
            self._tail(schema, write=True, versioned=action.instance)
            returns = action.output_schema or (self.read if action.instance else None)
            output, wrapped = _output(returns)
            tools.append(
                self.tool(
                    f"action_{action.name}",
                    operation="action",
                    permission=f"action:{action.name}",
                    target=action.name,
                    title=f"{d.name}: {action.name}",
                    description=_describe(
                        d, action.description or f"Run the {action.name} action."
                    ),
                    input_schema=schema.build(),
                    output_schema=output,
                    instance=action.instance,
                    wraps_result=wrapped,
                )
            )
        return tools

    def _related_tool(self, relation: str, ref: Any) -> ResourceTool:
        owner = self.manager.registry.get(ref.owner)
        owner_read = owner.schemas.get("read")
        schema = _Schema()
        self._id(schema)
        schema.add("offset", {"type": "integer", "minimum": 0, "default": 0})
        schema.add(
            "limit",
            {"type": "integer", "minimum": 1, "maximum": owner.max_page_size, "default": 50},
        )
        if owner.sortable:
            order = [f for name in owner.sortable for f in (name, f"-{name}")]
            schema.add("order_by", {"type": "array", "items": {"type": "string", "enum": order}})
        filterable = [f for f in owner.filterable if f != ref.field]
        if filterable:
            filters = _Schema()
            for field_name in filterable:
                annotation = owner_read.model_fields[field_name].annotation if owner_read else str
                filters.add(field_name, filters.type(annotation))
            schema.defs.update(filters.defs)
            schema.add(
                "filters",
                {"type": "object", "properties": filters.properties, "additionalProperties": False},
            )
        self._expand(schema, owner)
        self._tail(schema, write=False)
        return self.tool(
            relation,
            operation="related",
            permission="list",
            permission_resource=ref.owner,
            target=relation,
            title=f"{ref.owner} of a {self.d.name} record",
            description=_describe(
                self.d,
                (
                    f"The {ref.owner} records whose {ref.field} is this {self.d.name} record"
                    if ref.field
                    else f"The {ref.owner} linked to this {self.d.name} record (through {ref.join})"
                )
                + f", paged. The {self.d.name} record must be readable.",
            ),
            input_schema=schema.build(),
            output_schema=_page_output(owner_read, list(owner.references)),
        )

    def _machine_tools(self, read_output: dict[str, Any] | None) -> list[ResourceTool]:
        d = self.d
        info = d.machine.info  # type: ignore[union-attr]
        states = _state_names(d)
        machine_doc = _first_paragraph(inspect.getdoc(d.state_machine))
        tools: list[ResourceTool] = []

        schema = _Schema()
        self._id(schema)
        self._tail(schema, write=False)
        tools.append(
            self.tool(
                "allowed_transitions",
                operation="allowed_transitions",
                permission="get",
                title=f"Allowed transitions of {d.name}",
                description=_describe(
                    d,
                    "Transitions the caller may run on the record now, from its current "
                    "state. Call it before a transition tool.",
                    [machine_doc],
                ),
                input_schema=schema.build(),
                output_schema={
                    "type": "object",
                    "properties": {"transitions": {"type": "array", "items": {"type": "string"}}},
                    "required": ["transitions"],
                },
            )
        )

        if self.manager.has_history and d.resource_cls.audit:
            from resource_engine.audit.history import StateChange

            schema = _Schema()
            self._id(schema)
            self._tail(schema, write=False)
            change = StateChange.model_json_schema(mode="serialization")
            tools.append(
                self.tool(
                    "history",
                    operation="history",
                    permission="get",
                    title=f"State history of {d.name}",
                    description=_describe(
                        d,
                        "The record's creation and committed transitions, oldest first, "
                        "with who made them and when.",
                    ),
                    input_schema=schema.build(),
                    output_schema={
                        "type": "object",
                        "properties": {"changes": {"type": "array", "items": change}},
                        "required": ["changes"],
                    },
                )
            )

        events: dict[str, list[tuple[str, str]]] = {}
        for t in info.transitions:
            events.setdefault(t.event, []).append((states[t.source], states[t.target]))
        for event in info.events:
            moves = events.get(event, [])
            schema = _Schema()
            self._id(schema)
            self._data(schema, d.transition_schemas.get(event), "Transition input")
            self._tail(schema, write=True, versioned=True)
            where = ", ".join(f"{src} → {dst}" for src, dst in moves)
            tools.append(
                self.tool(
                    f"transition_{event}",
                    operation="transition",
                    permission=f"transition:{event}",
                    target=event,
                    title=f"{d.name}: {event}",
                    description=_describe(
                        d,
                        f"Run the {event} transition on a {d.name} record"
                        + (f" ({where})." if where else "."),
                        [machine_doc],
                    ),
                    input_schema=schema.build(),
                    output_schema=read_output,
                )
            )
        return tools


def _matches(name: str, patterns: Collection[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def build_tools(
    registry: ResourceRegistry,
    manager: ResourceManager,
    *,
    resources: Collection[str] | None = None,
    include: Collection[str] | None = None,
    exclude: Collection[str] = (),
) -> dict[str, ResourceTool]:
    """Tools for every registered resource (or ``resources``), by name.

    ``include`` and ``exclude`` take tool names or shell patterns
    (``invoices_*``, ``*_delete``); ``exclude`` wins.
    """
    tools: dict[str, ResourceTool] = {}
    for definition in registry:
        if resources is not None and definition.name not in resources:
            continue
        for tool in _Builder(definition, manager).build():
            if include is not None and not _matches(tool.name, include):
                continue
            if _matches(tool.name, exclude):
                continue
            if tool.name in tools:
                raise ConfigurationError(
                    f"Two MCP tools are named {tool.name!r}",
                    hint="Rename a transition or action, or exclude one of the tools.",
                    doc="docs/mcp.md#tools",
                )
            tools[tool.name] = tool
    return tools
