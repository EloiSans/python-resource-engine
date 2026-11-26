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
class TimerDoc:
    """A timed transition (``Resource.timers``)."""

    event: str
    after: str
    since: str
    states: list[str]
    description: str | None = None


@dataclass
class StateMachineDoc:
    name: str
    qualname: str
    state_field: str
    doc: DocText
    states: list[StateDoc]
    transitions: list[TransitionDoc]
    timers: list[TimerDoc] = field(default_factory=list)
    since_field: str | None = None
    machine: str = ""  # its name on the resource (Resource.machines)


@dataclass
class ActionDoc:
    name: str
    qualname: str
    instance: bool
    doc: DocText
    input_schema: str | None
    output_schema: str | None


@dataclass
class ReferenceDoc:
    """A reference to another resource (``Resource.references``)."""

    name: str
    field: str
    resource: str
    on_delete: str
    description: str | None = None
    related_name: str | None = None


@dataclass
class ReactionDoc:
    """An event a process reacts to (``Process.reacts``)."""

    event: str
    when: str
    key: str
    start: bool
    handler: str
    description: str | None = None


@dataclass
class EventDoc:
    """A domain event the resource declares (``Resource.events``)."""

    name: str
    qualname: str
    doc: DocText
    fields: list[FieldDoc]


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
    state_machines: list[StateMachineDoc]
    filterable: list[str] = field(default_factory=list)
    sortable: list[str] = field(default_factory=list)
    documents: list[SlotDoc] = field(default_factory=list)
    restricted_fields: list[str] = field(default_factory=list)
    tenant_field: str | None = None
    version_field: str | None = None
    require_version: bool = False
    audit: bool = True
    audit_reads: bool = False
    audit_exclude: list[str] = field(default_factory=list)
    events: list[EventDoc] = field(default_factory=list)
    references: list[ReferenceDoc] = field(default_factory=list)
    referenced_by: list[ReferenceDoc] = field(default_factory=list)
    #: Many-to-many relations: ``name`` lists ``resource`` through the join ``field``.
    links: list[ReferenceDoc] = field(default_factory=list)
    #: For process managers: the correlation field and the events they react to.
    correlation: str | None = None
    reacts: list[ReactionDoc] = field(default_factory=list)
    #: Polymorphic resources: a base's discriminator and subtypes (by identity),
    #: or the base a subtype extends and its identity.
    discriminator: str | None = None
    subtypes: dict[str, str] = field(default_factory=dict)
    extends: str | None = None
    identity: str | None = None
    #: View resources: "custom", or the source of an aggregate, its groups and values.
    view: str | None = None
    view_group_by: list[str] = field(default_factory=list)
    view_values: dict[str, str] = field(default_factory=dict)


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
    audit_resource: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_machine(self: ResourceDoc) -> StateMachineDoc | None:
    """The first (or only) state machine."""
    return self.state_machines[0] if self.state_machines else None


ResourceDoc.state_machine = property(_first_machine)  # type: ignore[attr-defined]
