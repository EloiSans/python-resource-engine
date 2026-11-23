"""Format-independent functional documentation model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from resource_engine_devtools.documentation.docstrings import DocText


@dataclass
class FieldDoc:
    name: str
    type: str
    required: bool
    description: str | None = None


@dataclass
class SchemaDoc:
    name: str
    role: str
    fields: list[FieldDoc]
    doc: DocText


@dataclass
class HookDoc:
    point: str
    name: str
    qualname: str
    external_effect: bool
    doc: DocText


@dataclass
class OperationDoc:
    name: str
    kind: str  # crud | transition | action
    method: str
    path: str
    doc: DocText
    input_schema: str | None = None
    output_schema: str | None = None


@dataclass
class StateDoc:
    id: str
    name: str
    initial: bool
    final: bool
    description: str | None


@dataclass
class TransitionDoc:
    event: str
    source: str
    target: str
    description: str | None


@dataclass
class StateMachineDoc:
    name: str
    qualname: str
    state_field: str
    doc: DocText
    states: list[StateDoc]
    transitions: list[TransitionDoc]


@dataclass
class ActionDoc:
    name: str
    qualname: str
    instance: bool
    doc: DocText
    input_schema: str | None
    output_schema: str | None


@dataclass
class SlotDoc:
    name: str
    description: str | None
    content_types: list[str]
    max_bytes: int
    multiple: bool
    upload: str
    required_for: list[str]
    processing: bool


@dataclass
class ResourceDoc:
    name: str
    qualname: str
    module: str
    model: str
    doc: DocText
    operations: list[OperationDoc]
    schemas: list[SchemaDoc]
    hooks: list[HookDoc]
    actions: list[ActionDoc]
    state_machine: StateMachineDoc | None
    filterable: list[str] = field(default_factory=list)
    sortable: list[str] = field(default_factory=list)
    documents: list[SlotDoc] = field(default_factory=list)


@dataclass
class SystemRoleDoc:
    name: str
    description: str | None
    grants: list[str]
    actions: list[str]  # catalogue actions the grants cover


@dataclass
class DocumentationModel:
    resources: list[ResourceDoc]
    engine_version: str
    actions: list[str] = field(default_factory=list)
    system_roles: list[SystemRoleDoc] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
