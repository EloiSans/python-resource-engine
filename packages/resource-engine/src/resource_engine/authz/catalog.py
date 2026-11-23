"""What policies can talk about: actions and attributes, derived from the registry.

Published to front ends (``GET /authz/catalog``) to build policy editors.
"""

from __future__ import annotations

import enum
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from resource_engine.authz.conditions import OPERATORS

if typing.TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry
    from resource_engine.application.resource import ResourceDefinition


@dataclass(frozen=True)
class AttributeInfo:
    path: str
    type: str
    enum: list[Any] | None = None
    description: str | None = None


@dataclass(frozen=True)
class ActionInfo:
    name: str
    resource: str
    kind: str  # crud, transition, action, attach, detach, download
    qualifier: str | None = None
    description: str | None = None
    wildcards: bool = True  # False: only explicit grants match (sensitive resources)
    input: list[AttributeInfo] = field(default_factory=list)


SUBJECT_ATTRIBUTES = [
    AttributeInfo("subject.id", "string", description="provider:subject"),
    AttributeInfo("subject.subject", "string"),
    AttributeInfo("subject.provider", "string"),
    AttributeInfo("subject.roles", "list", description="Application roles"),
    AttributeInfo("subject.groups", "list"),
    AttributeInfo("subject.scopes", "list"),
    AttributeInfo("subject.tenant", "string"),
    AttributeInfo("subject.client_id", "string"),
    AttributeInfo("subject.service", "boolean"),
    AttributeInfo("subject.email", "string"),
]
CONTEXT_ATTRIBUTES = [
    AttributeInfo("context.operation", "string"),
    AttributeInfo("context.action", "string"),
    AttributeInfo("context.now", "datetime"),
]


def _type_of(annotation: Any) -> tuple[str, list[Any] | None]:
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _type_of(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _type_of(args[0]) if len(args) == 1 else ("any", None)
    if origin is typing.Literal:
        return "string", list(typing.get_args(annotation))
    if origin in (list, set, frozenset, tuple):
        return "list", None
    if origin in (dict, Mapping):
        return "object", None
    if isinstance(annotation, type):
        if issubclass(annotation, bool):
            return "boolean", None
        if issubclass(annotation, enum.Enum):
            return "string", [m.value for m in annotation]
        if issubclass(annotation, int | float | Decimal):
            return "number", None
        if issubclass(annotation, datetime):
            return "datetime", None
        if issubclass(annotation, date):
            return "date", None
        if issubclass(annotation, str):
            return "string", None
        if issubclass(annotation, BaseModel | dict):
            return "object", None
    return "any", None


def schema_attributes(prefix: str, schema: type[BaseModel] | None) -> list[AttributeInfo]:
    if schema is None:
        return []
    out = []
    for name, f in schema.model_fields.items():
        kind, values = _type_of(f.annotation)
        out.append(AttributeInfo(f"{prefix}.{name}", kind, values, f.description))
    return out


@dataclass
class Catalog:
    actions: dict[str, ActionInfo]
    resource_attributes: dict[str, list[AttributeInfo]]

    @classmethod
    def from_registry(cls, registry: ResourceRegistry) -> Catalog:
        actions: dict[str, ActionInfo] = {}
        attributes: dict[str, list[AttributeInfo]] = {}
        for d in registry:
            attributes[d.name] = cls._resource_attributes(d)
            for action in cls._actions(d):
                actions[action.name] = action
        return cls(actions, attributes)

    @staticmethod
    def _resource_attributes(d: ResourceDefinition) -> list[AttributeInfo]:
        attrs = schema_attributes("resource", d.schemas.get("read"))
        if d.machine is not None and d.state_field:
            states = [s.id for s in d.machine.info.states]
            attrs = [a for a in attrs if a.path != f"resource.{d.state_field}"]
            attrs.append(AttributeInfo(f"resource.{d.state_field}", "string", states, "State"))
        return attrs

    @staticmethod
    def _actions(d: ResourceDefinition) -> Iterable[ActionInfo]:
        wild = getattr(d.resource_cls, "authz_wildcards", True)
        for op in d.operations:
            schema = d.schemas.get(op) if op in ("create", "update") else None
            yield ActionInfo(
                f"{d.name}:{op}",
                d.name,
                "crud",
                description=d.operation_docs.get(op),
                wildcards=wild,
                input=schema_attributes("input", schema),
            )
        if d.machine is not None:
            for event in d.machine.info.events:
                yield ActionInfo(
                    f"{d.name}:transition:{event}",
                    d.name,
                    "transition",
                    event,
                    wildcards=wild,
                    input=schema_attributes("input", d.transition_schemas.get(event)),
                )
        for a in d.actions.values():
            yield ActionInfo(
                f"{d.name}:action:{a.name}",
                d.name,
                "action",
                a.name,
                description=(a.description or "").split("\n", 1)[0] or None,
                wildcards=wild,
                input=schema_attributes("input", a.input_schema),
            )
        for slot in d.document_slots.values():
            for kind in ("attach", "detach", "download"):
                yield ActionInfo(
                    f"{d.name}:{kind}:{slot.name}",
                    d.name,
                    kind,
                    slot.name,
                    description=slot.description,
                    wildcards=wild,
                )

    # ------------------------------------------------------------- queries

    def matching(self, pattern: str, *, include_sensitive: bool = False) -> list[str]:
        return [
            name
            for name, info in self.actions.items()
            if action_matches(pattern, name, wildcards=info.wildcards or include_sensitive)
        ]

    def attribute_types(self, actions: Iterable[str]) -> dict[str, str]:
        """Attributes a condition may use for all of ``actions`` (intersection of
        resource and input attributes; subject and context always)."""
        common: dict[str, str] | None = None
        for name in actions:
            info = self.actions[name]
            attrs = {
                a.path: a.type for a in [*self.resource_attributes[info.resource], *info.input]
            }
            common = attrs if common is None else {k: v for k, v in common.items() if k in attrs}
        out = {a.path: a.type for a in [*SUBJECT_ATTRIBUTES, *CONTEXT_ATTRIBUTES]}
        out.update(common or {})
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "operators": list(OPERATORS),
            "subject": [asdict(a) for a in SUBJECT_ATTRIBUTES],
            "context": [asdict(a) for a in CONTEXT_ATTRIBUTES],
            "resources": {
                name: {
                    "attributes": [asdict(a) for a in attrs],
                    "actions": [asdict(a) for a in self.actions.values() if a.resource == name],
                }
                for name, attrs in self.resource_attributes.items()
            },
        }


def action_matches(pattern: str, action: str, *, wildcards: bool = True) -> bool:
    """``*`` matches one segment; a trailing ``*`` matches the rest.

    With ``wildcards=False`` (sensitive resources) the resource segment must be
    named explicitly.
    """
    p, a = pattern.split(":"), action.split(":")
    if not wildcards and p[0] != a[0]:
        return False
    for i, part in enumerate(p):
        if part == "*" and i == len(p) - 1:
            return len(a) >= len(p)
        if i >= len(a) or (part != "*" and part != a[i]):
            return False
    return len(a) == len(p)
