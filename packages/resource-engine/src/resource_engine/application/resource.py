"""Declarative resource definitions."""

from __future__ import annotations

import dataclasses
import inspect
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel
from statemachine import StateMachine

from resource_engine.application.actions import Action
from resource_engine.application.hooks import (
    Hook,
    HookDispatcher,
    HookKey,
    HookPoint,
    normalize_hooks,
)
from resource_engine.application.relations import (
    Reference,
    ReferenceDefinition,
    normalize_link,
    normalize_references,
)
from resource_engine.application.slots import DocumentSlot
from resource_engine.application.state_machines import Machine, MachineRunner
from resource_engine.application.timers import Timer, TimerDefinition, normalize_timers
from resource_engine.application.views import check_view
from resource_engine.domain.events import DomainEvent
from resource_engine.errors import ConfigurationError, StateFieldExposed

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext

CRUD_OPERATIONS = ("list", "get", "create", "update", "delete")

# Schemas each CRUD operation needs.
_REQUIRED_SCHEMAS = {
    "list": ("read",),
    "get": ("read",),
    "create": ("create", "read"),
    "update": ("update", "read"),
    "delete": (),
}


def _default_name(cls_name: str) -> str:
    base = cls_name.removesuffix("Resource") or cls_name
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
    return snake if snake.endswith("s") else f"{snake}s"


class Resource:
    """Declarative description of a resource.

    Subclass it and set class attributes; nothing is executed at import time.
    The definition is validated when registered in a ``ResourceRegistry``.

    Attributes:
        name: Public name, used in URLs and registry lookups. Defaults to the
            pluralised snake_case class name without ``Resource``
            (``InvoiceResource`` → ``invoices``).
        model: Domain entity class (a plain class or dataclass, not an ORM model).
        id_field / id_type: Identifier attribute of the entity and its type.
        schemas: Pydantic schemas per role: ``create``, ``update``, ``read``.
        operations: Enabled CRUD operations, a subset of
            ``list, get, create, update, delete``.
        operation_docs: Functional description of each CRUD operation.
        hooks: ``{"before_create": [fn, ...], "after_transition:approve": fn}``.
        state_machine: A ``python-statemachine`` class, or ``None``.
        state_field: Entity attribute that stores the state (default ``status``).
        machines: Several state machines, by name, each a ``Machine`` with its
            own field (an invoice's ``approval`` and ``payment``). Instead of
            ``state_machine``; transition names are unique across them.
        transition_schemas: Optional input schema per transition event.
        actions: ``Action`` subclasses exposed by this resource.
        filterable / sortable: Fields accepted by ``list`` filters and ordering.
        max_page_size: Upper bound for ``list`` page size.
        documents: ``DocumentSlot`` declarations (files attached to entities).
        authz_wildcards: ``False`` for sensitive resources: wildcard grants
            (``*:list``) do not match their actions, only explicit ones.
        expose_transitions: Publish transitions through entry adapters (HTTP).
            ``False`` keeps them internal to the engine and its handlers.
        restricted_fields: Fields that need an explicit grant to be read or
            written (``<resource>:read:<field>`` / ``<resource>:write:<field>``)
            when an authorizer is configured.
        tenant_field: Entity attribute holding the tenant. Every operation is then
            confined to the caller's tenant (see docs/tenancy.md).
        version_field: Integer attribute for optimistic concurrency: writes check
            the version the client saw and bump it (see docs/concurrency.md).
        require_version: Refuse writes that do not send a version (RE-E106).
        audit: ``False`` leaves the resource out of the audit trail.
        audit_reads: Also audit ``get`` and ``list``.
        audit_exclude: Fields recorded as changed without their values (restricted
            fields always are).
        events: Domain events (``DomainEvent`` dataclasses) that the resource's
            actions and hooks record with ``ctx.record_event``. The engine's own
            events (created, updated, transitions...) are implicit. Listed in the
            generated docs and the AsyncAPI document; ``check`` flags undeclared
            ones (RE-C006).
        references: Fields holding ids of other resources' records:
            ``{"customer_id": Reference("customers")}``. Integrity on writes,
            ``on_delete`` behaviour, ``expand`` and related filters (see
            docs/relationships.md).
        internal_operations: Enabled CRUD operations that only code runs
            (the manager, processes, jobs): entry adapters (HTTP, GraphQL, MCP)
            do not expose them.
        state_since_field: Datetime attribute the engine sets when the record
            enters a state (create and every transition): when it got there.
        timers: ``Timer`` deadlines: transitions run by the worker once a record
            has waited long enough in a state (see docs/timers.md).
        link: On a join resource, the two reference fields it links
            (``("invoice_id", "tag_id")``): invoices get a ``tags`` relation and
            tags an ``invoices`` one (many-to-many).
        link_names: Names of those relations, by link field
            (``{"invoice_id": "labels"}``: the invoices' relation is ``labels``).
        discriminator: On a base resource with subtypes (``requests``): the
            field holding each record's subtype. The base lists and reads every
            type and runs its state machines and actions on them; records are
            created, updated and deleted through the subtypes (see
            docs/polymorphism.md).
        extends: On a subtype: the base resource class. The subtype's model
            subclasses the base model, its read schema includes the base's
            fields, and it shares the base's ids, tenancy, versioning, state
            machines and CRUD hooks.
        identity: The subtype's value in the discriminator field (default: its
            name).
        cache_ttl: Seconds the manager's cache (``ResourceManager(cache=)``)
            keeps records read by id; writes through the manager forget them
            (see docs/caching.md). ``None``: not cached.
        cache_control: ``Cache-Control`` of the HTTP ``GET`` answers
            (``"private, max-age=30"``); the default lets clients store
            answers but revalidate them (ETag, 304).
        view: A read-only resource computed by a query: an ``Aggregate`` over
            another resource (computed by every storage adapter), or ``True``
            for a custom view the storage adapter defines (see docs/views.md).
    """

    name: ClassVar[str | None] = None
    model: ClassVar[type[Any]]
    id_field: ClassVar[str] = "id"
    id_type: ClassVar[type[Any]] = int
    schemas: ClassVar[Mapping[str, type[BaseModel]]] = {}
    operations: ClassVar[Sequence[str]] = ()
    operation_docs: ClassVar[Mapping[str, str]] = {}
    hooks: ClassVar[Mapping[str | HookPoint, Hook | Sequence[Hook]]] = {}
    state_machine: ClassVar[type[StateMachine] | None] = None
    machines: ClassVar[Mapping[str, Machine]] = {}
    state_field: ClassVar[str] = "status"
    transition_schemas: ClassVar[Mapping[str, type[BaseModel]]] = {}
    actions: ClassVar[Sequence[type[Action]]] = ()
    filterable: ClassVar[Sequence[str]] = ()
    sortable: ClassVar[Sequence[str]] = ()
    max_page_size: ClassVar[int] = 100
    documents: ClassVar[Sequence[DocumentSlot]] = ()
    expose_transitions: ClassVar[bool] = True
    authz_wildcards: ClassVar[bool] = True
    restricted_fields: ClassVar[Sequence[str]] = ()
    tenant_field: ClassVar[str | None] = None
    version_field: ClassVar[str | None] = None
    require_version: ClassVar[bool] = False
    audit: ClassVar[bool] = True
    audit_reads: ClassVar[bool] = False
    audit_exclude: ClassVar[Sequence[str]] = ()
    events: ClassVar[Sequence[type[DomainEvent]]] = ()
    references: ClassVar[Mapping[str, Reference]] = {}
    link: ClassVar[Sequence[str] | None] = None
    state_since_field: ClassVar[str | None] = None
    timers: ClassVar[Sequence[Timer]] = ()
    internal_operations: ClassVar[Sequence[str]] = ()
    link_names: ClassVar[Mapping[str, str]] = {}
    discriminator: ClassVar[str | None] = None
    extends: ClassVar[type[Resource] | None] = None
    identity: ClassVar[str | None] = None
    view: ClassVar[Any] = None
    cache_ttl: ClassVar[float | None] = None
    cache_control: ClassVar[str | None] = None

    # --- Mapping between schemas and entities. Override when needed. ---

    @classmethod
    def build_entity(cls, data: BaseModel, ctx: OperationContext) -> Any:
        """Build a new entity from validated ``create`` input."""
        return cls.model(**data.model_dump())

    @classmethod
    def apply_update(cls, entity: Any, data: BaseModel, ctx: OperationContext) -> None:
        """Apply validated ``update`` input to an entity (only fields actually sent)."""
        protected = {cls.id_field, cls.version_field, *cls.definition().state_fields}
        for key, value in data.model_dump(exclude_unset=True).items():
            if key not in protected:
                setattr(entity, key, value)

    @classmethod
    def to_read(cls, entity: Any, ctx: OperationContext) -> Any:
        """Convert an entity into the ``read`` schema."""
        return cls.schemas["read"].model_validate(entity, from_attributes=True)

    # --- Definition ---

    @classmethod
    def definition(cls) -> ResourceDefinition:
        cached = cls.__dict__.get("_definition")
        if cached is None:
            cached = ResourceDefinition.from_resource(cls)
            cls._definition = cached  # type: ignore[attr-defined]
        return cached


@dataclass(frozen=True)
class ActionDefinition:
    name: str
    action_cls: type[Action]
    input_schema: type[BaseModel] | None
    output_schema: type[BaseModel] | None
    instance: bool
    description: str | None


@dataclass(frozen=True)
class ResourceDefinition:
    """Validated, normalised form of a ``Resource`` subclass."""

    name: str
    resource_cls: type[Resource]
    model: type[Any]
    id_field: str
    id_type: type[Any]
    schemas: Mapping[str, type[BaseModel]]
    operations: tuple[str, ...]
    operation_docs: Mapping[str, str]
    hooks: Mapping[HookKey, tuple[Hook, ...]]
    state_machine: type[StateMachine] | None
    state_field: str | None
    transition_schemas: Mapping[str, type[BaseModel]]
    actions: Mapping[str, ActionDefinition]
    filterable: tuple[str, ...]
    sortable: tuple[str, ...]
    max_page_size: int
    description: str | None
    document_slots: Mapping[str, DocumentSlot]
    expose_transitions: bool
    restricted_fields: tuple[str, ...]
    tenant_field: str | None
    version_field: str | None
    require_version: bool
    events: tuple[type[DomainEvent], ...]
    references: Mapping[str, ReferenceDefinition]
    link: tuple[str, str] | None
    link_names: Mapping[str, str]
    state_since_field: str | None
    timers: tuple[TimerDefinition, ...]
    internal_operations: tuple[str, ...]
    dispatcher: HookDispatcher = field(repr=False, compare=False)
    #: The first (or only) machine; ``machines`` has them all, by name.
    machine: MachineRunner | None = field(repr=False, compare=False)
    machines: Mapping[str, MachineRunner] = field(default_factory=dict, repr=False, compare=False)
    discriminator: str | None = None
    identity: str | None = None
    view: Any = None
    cache_ttl: float | None = None
    cache_control: str | None = None
    #: The base resource's definition, on a subtype.
    parent: ResourceDefinition | None = field(default=None, repr=False, compare=False)

    def enabled(self, operation: str) -> bool:
        return operation in self.operations

    def exposed(self, operation: str) -> bool:
        """Enabled and offered by entry adapters (not an internal operation)."""
        return operation in self.operations and operation not in self.internal_operations

    @property
    def transitions(self) -> tuple[str, ...]:
        """Every machine's transition events, in declaration order."""
        return tuple(e for m in self.machines.values() for e in m.info.events)

    @property
    def record_machines(self) -> Mapping[str, MachineRunner]:
        """The machines a record of this resource has: a subtype's are its base's."""
        return self.parent.machines if self.parent is not None else self.machines

    @property
    def state_fields(self) -> tuple[str, ...]:
        return tuple(m.state_field for m in self.record_machines.values())

    @property
    def family(self) -> str:
        """The resource whose table holds the records: the base for a subtype."""
        return self.parent.name if self.parent is not None else self.name

    def machine_for(self, event: str) -> MachineRunner | None:
        """The machine that has the transition ``event``."""
        return next((m for m in self.machines.values() if event in m.info.events), None)

    @classmethod
    def from_resource(cls, resource_cls: type[Resource]) -> ResourceDefinition:
        owner = resource_cls.__qualname__
        model = getattr(resource_cls, "model", None)
        if model is None:
            raise ConfigurationError(
                f"{owner} does not declare 'model'",
                hint="Set `model = <domain entity class>` on the resource.",
                doc="docs/resources.md#declaring-a-resource",
            )
        name = resource_cls.name or _default_name(resource_cls.__name__)
        parent = _parent(owner, resource_cls, model)

        operations = tuple(resource_cls.operations)
        unknown = [op for op in operations if op not in CRUD_OPERATIONS]
        if unknown:
            raise ConfigurationError(
                f"{owner} enables unknown operations {unknown}",
                hint=f"CRUD operations are {', '.join(CRUD_OPERATIONS)}. "
                "Business operations must be Actions or state transitions.",
                doc="docs/resources.md#operations",
            )

        schemas = dict(resource_cls.schemas)
        for op in operations:
            for role in _REQUIRED_SCHEMAS[op]:
                schema = schemas.get(role)
                if schema is None:
                    raise ConfigurationError(
                        f"{owner} enables '{op}' but has no '{role}' schema",
                        hint=f'Add `schemas = {{"{role}": <PydanticModel>, ...}}`.',
                        doc="docs/resources.md#schemas",
                    )
                if not (inspect.isclass(schema) and issubclass(schema, BaseModel)):
                    raise ConfigurationError(f"{owner}: schema '{role}' must be a Pydantic model")

        if parent is not None and (
            resource_cls.state_machine
            or resource_cls.machines
            or resource_cls.timers
            or resource_cls.transition_schemas
        ):
            raise ConfigurationError(
                f"{owner}: state machines, timers and transitions of a subtype belong to "
                f"its base {parent.name!r}",
                hint="Declare them on the base resource; they run on every subtype's records.",
                doc="docs/polymorphism.md",
            )
        runners = _machines(owner, name, resource_cls, schemas)
        machine = next(iter(runners.values()), None)
        sm = machine.machine_cls if machine else None
        state_field = machine.state_field if machine else None
        events_of = {e: r for r in runners.values() for e in r.info.events}

        transition_schemas = dict(resource_cls.transition_schemas)
        if transition_schemas and machine is None:
            raise ConfigurationError(f"{owner} declares transition_schemas without a state_machine")
        for event in transition_schemas:
            if machine and event not in events_of:
                raise ConfigurationError(
                    f"{owner}: transition_schemas references unknown transition {event!r}",
                    hint=f"Known transitions: {', '.join(events_of)}",
                )

        actions: dict[str, ActionDefinition] = {}
        for action_cls in resource_cls.actions:
            if not (inspect.isclass(action_cls) and issubclass(action_cls, Action)):
                raise ConfigurationError(f"{owner}: {action_cls!r} is not an Action subclass")
            action_name = action_cls.action_name()
            if action_name in actions:
                raise ConfigurationError(f"{owner}: duplicate action name {action_name!r}")
            actions[action_name] = ActionDefinition(
                name=action_name,
                action_cls=action_cls,
                input_schema=action_cls.input_schema,
                output_schema=action_cls.output_schema,
                instance=action_cls.instance,
                description=inspect.getdoc(action_cls),
            )

        slots: dict[str, DocumentSlot] = {}
        for slot in resource_cls.documents:
            if not isinstance(slot, DocumentSlot):
                raise ConfigurationError(f"{owner}: documents must contain DocumentSlot objects")
            problems = slot.problems()
            if slot.name in slots:
                problems.append(f"duplicate slot {slot.name!r}")
            for event in slot.required_for:
                if machine is None or event not in machine.info.events:
                    problems.append(f"slot {slot.name!r} requires unknown transition {event!r}")
            if problems:
                raise ConfigurationError(
                    f"{owner}: invalid document slots: {'; '.join(problems)}",
                    doc="docs/documents.md#slots",
                )
            slots[slot.name] = slot

        hooks = normalize_hooks(resource_cls.hooks, owner=owner)
        for key in hooks:
            if key.qualifier is None:
                continue
            if key.point in (HookPoint.BEFORE_TRANSITION, HookPoint.AFTER_TRANSITION):
                known: tuple[str, ...] = machine.info.events if machine else ()
            elif key.point in (
                HookPoint.BEFORE_ATTACH,
                HookPoint.AFTER_ATTACH,
                HookPoint.BEFORE_DETACH,
                HookPoint.AFTER_DETACH,
            ):
                known = tuple(slots)
            else:
                known = tuple(actions)
            if key.qualifier not in known:
                raise ConfigurationError(
                    f"{owner}: hook {str(key)!r} references unknown {key.qualifier!r}",
                    hint=f"Known names: {', '.join(known) or '(none)'}",
                )

        read_fields = schemas["read"].model_fields if "read" in schemas else {}
        for attr in ("filterable", "sortable"):
            for fld in getattr(resource_cls, attr):
                if read_fields and fld not in read_fields:
                    raise ConfigurationError(
                        f"{owner}: {attr} field {fld!r} is not part of the read schema"
                    )

        known_fields = set(read_fields)
        for role in ("create", "update"):
            if role in schemas:
                known_fields |= set(schemas[role].model_fields)
        tenant_field = _inherited(owner, "tenant_field", resource_cls, parent)
        if tenant_field is not None:
            if tenant_field == resource_cls.id_field:
                raise ConfigurationError(f"{owner}: the id field cannot be the tenant field")
            if read_fields and tenant_field not in read_fields:
                raise ConfigurationError(
                    f"{owner}: tenant field {tenant_field!r} is not part of the read schema"
                )
            if "update" in schemas and tenant_field in schemas["update"].model_fields:
                raise ConfigurationError(
                    f"{owner}: the update schema must not contain the tenant field "
                    f"{tenant_field!r}",
                    hint="Records never move between tenants.",
                    doc="docs/tenancy.md",
                )
        version_field = _inherited(owner, "version_field", resource_cls, parent)
        if version_field is not None:
            reserved = {
                resource_cls.id_field: "id",
                state_field: "state",
                tenant_field: "tenant",
            }
            if version_field in reserved:
                raise ConfigurationError(
                    f"{owner}: the {reserved[version_field]} field cannot be the version field"
                )
            if read_fields and version_field not in read_fields:
                raise ConfigurationError(
                    f"{owner}: version field {version_field!r} is not part of the read schema",
                    hint="Expose it so clients can send it back (ETag / If-Match).",
                    doc="docs/concurrency.md",
                )
            if "create" in schemas and version_field in schemas["create"].model_fields:
                raise ConfigurationError(
                    f"{owner}: the create schema must not contain the version field "
                    f"{version_field!r}",
                    hint="New records start at version 1.",
                    doc="docs/concurrency.md",
                )
        elif resource_cls.require_version and parent is None:
            raise ConfigurationError(f"{owner}: require_version needs a version_field")
        for fld in resource_cls.audit_exclude:
            if fld not in known_fields:
                raise ConfigurationError(
                    f"{owner}: audit_exclude field {fld!r} is not part of the read or write schemas"
                )
        for fld in resource_cls.restricted_fields:
            if fld == resource_cls.id_field:
                raise ConfigurationError(f"{owner}: the id field cannot be restricted")
            if fld not in known_fields:
                raise ConfigurationError(
                    f"{owner}: restricted field {fld!r} is not part of the read or write schemas"
                )

        events = tuple(resource_cls.events)
        for event in events:
            if not (
                isinstance(event, type)
                and issubclass(event, DomainEvent)
                and dataclasses.is_dataclass(event)
            ):
                raise ConfigurationError(
                    f"{owner}: events must be DomainEvent dataclasses, not {event!r}",
                    hint="Declare `@dataclass(frozen=True, kw_only=True) class X(DomainEvent)`.",
                    doc="docs/messaging.md#declaring-events",
                )
        if len({e.__name__ for e in events}) != len(events):
            raise ConfigurationError(
                f"{owner}: two declared events share a class name",
                hint="The class name is the event type on the wire; rename one.",
                doc="docs/messaging.md#declaring-events",
            )

        internal = tuple(resource_cls.internal_operations)
        if set(internal) - set(operations):
            raise ConfigurationError(
                f"{owner}: internal_operations {sorted(set(internal) - set(operations))} "
                "are not enabled operations",
                hint="List them in operations too; internal ones are only hidden from adapters.",
            )
        for runner in runners.values():
            _check_since_field(owner, runner.since_field, schemas, known_fields)
        since_field = machine.since_field if machine else None
        timers = normalize_timers(
            owner,
            name,
            resource_cls.timers,
            machines=runners,
            transition_schemas=transition_schemas,
            known_fields=known_fields,
        )
        references = normalize_references(name, owner, resource_cls.references, known_fields)
        description = resource_cls.__doc__ and inspect.cleandoc(resource_cls.__doc__)
        discriminator = _check_family(owner, resource_cls, parent, schemas, operations)
        if resource_cls.view is not None:
            _check_view_resource(owner, resource_cls, operations, runners, actions, slots)
            check_view(
                owner,
                set(read_fields),
                resource_cls.id_field,
                resource_cls.view,
            )
        if parent is not None:
            hooks = _merge_hooks(parent.hooks, hooks)
        return cls(
            name=name,
            resource_cls=resource_cls,
            model=model,
            id_field=parent.id_field if parent else resource_cls.id_field,
            id_type=parent.id_type if parent else resource_cls.id_type,
            schemas=schemas,
            operations=operations,
            operation_docs=dict(resource_cls.operation_docs),
            hooks=hooks,
            state_machine=sm,
            state_field=state_field,
            transition_schemas=transition_schemas,
            actions=actions,
            filterable=tuple(
                dict.fromkeys(
                    [*resource_cls.filterable, *([discriminator] if discriminator else [])]
                )
            ),
            sortable=tuple(resource_cls.sortable),
            max_page_size=resource_cls.max_page_size,
            description=description or None,
            document_slots=slots,
            expose_transitions=resource_cls.expose_transitions,
            restricted_fields=tuple(resource_cls.restricted_fields),
            tenant_field=tenant_field,
            version_field=version_field,
            require_version=parent.require_version if parent else resource_cls.require_version,
            events=events,
            references=references,
            link=normalize_link(owner, resource_cls.link, resource_cls.link_names, references),
            link_names=dict(resource_cls.link_names),
            state_since_field=since_field,
            timers=timers,
            internal_operations=internal,
            dispatcher=HookDispatcher(hooks),
            machine=machine,
            machines=runners,
            discriminator=discriminator,
            identity=(resource_cls.identity or name) if parent else None,
            parent=parent,
            view=resource_cls.view,
            cache_ttl=_cache_ttl(owner, resource_cls.cache_ttl),
            cache_control=resource_cls.cache_control,
        )


def _machines(
    owner: str, name: str, resource_cls: type[Resource], schemas: Mapping[str, type[BaseModel]]
) -> dict[str, MachineRunner]:
    """``machines`` (or the ``state_machine`` shorthand) as runners, validated."""
    declared = dict(resource_cls.machines)
    sm = resource_cls.state_machine
    if declared and sm is not None:
        raise ConfigurationError(
            f"{owner} declares both state_machine and machines",
            hint="Use machines for several; state_machine is the shorthand for one.",
            doc="docs/state-machines.md#several-machines",
        )
    if declared and resource_cls.state_since_field is not None:
        raise ConfigurationError(
            f"{owner}: with machines, set since_field on each Machine, not state_since_field",
            doc="docs/state-machines.md#several-machines",
        )
    if sm is None and not declared and resource_cls.state_since_field is not None:
        raise ConfigurationError(f"{owner}: state_since_field needs a state machine")
    if sm is not None:
        declared = {
            resource_cls.state_field: Machine(
                sm, field=resource_cls.state_field, since_field=resource_cls.state_since_field
            )
        }
    runners: dict[str, MachineRunner] = {}
    seen_fields: dict[str, str] = {}
    seen_events: dict[str, str] = {}
    for machine_name, spec in declared.items():
        where = f"{owner} machine {machine_name!r}"
        if not isinstance(spec, Machine):
            raise ConfigurationError(f"{where}: use Machine(<StateMachine class>, field=...)")
        if not (inspect.isclass(spec.machine) and issubclass(spec.machine, StateMachine)):
            raise ConfigurationError(
                f"{where}: state_machine must be a python-statemachine StateMachine subclass"
            )
        for used, other in ((spec.field, seen_fields), (spec.since_field, seen_fields)):
            if used is not None and used in other:
                raise ConfigurationError(
                    f"{where}: field {used!r} is already used by machine {other[used]!r}",
                    doc="docs/state-machines.md#several-machines",
                )
            if used is not None:
                other[used] = machine_name
        for role in ("create", "update"):
            schema = schemas.get(role)
            if schema is not None and spec.field in schema.model_fields:
                raise StateFieldExposed(
                    f"{owner}: the state field {spec.field!r} is part of the "
                    f"'{role}' schema {schema.__name__}",
                    hint="Remove it from the schema. State changes only through "
                    "transitions (POST /{id}/transitions/<event>).",
                    doc="docs/state-machines.md#protected-state",
                )
        runner = MachineRunner(
            spec.machine, spec.field, name, name=machine_name, since_field=spec.since_field
        )
        for event in runner.info.events:
            if event in seen_events:
                raise ConfigurationError(
                    f"{owner}: transition {event!r} is in machines {seen_events[event]!r} "
                    f"and {machine_name!r}",
                    hint="Transition names are unique per resource (routes, permissions "
                    "and GraphQL use them alone); rename one.",
                    doc="docs/state-machines.md#several-machines",
                )
            seen_events[event] = machine_name
        runners[machine_name] = runner
    return runners


def _check_since_field(
    owner: str,
    since_field: str | None,
    schemas: Mapping[str, type[BaseModel]],
    known_fields: set[str],
) -> None:
    if since_field is None:
        return
    if since_field not in known_fields:
        raise ConfigurationError(
            f"{owner}: state_since_field {since_field!r} is not part of the read schema",
            doc="docs/timers.md",
        )
    for role in ("create", "update"):
        if role in schemas and since_field in schemas[role].model_fields:
            raise ConfigurationError(
                f"{owner}: state_since_field {since_field!r} is part of the {role!r} "
                "schema; the engine sets it",
                doc="docs/timers.md",
            )


def _parent(owner: str, resource_cls: type[Resource], model: Any) -> ResourceDefinition | None:
    """A subtype's base definition (``extends``), validated."""
    base = resource_cls.extends
    if base is None:
        if resource_cls.identity is not None:
            raise ConfigurationError(f"{owner}: identity is for subtypes (set extends)")
        return None
    if not (inspect.isclass(base) and issubclass(base, Resource)):
        raise ConfigurationError(f"{owner}: extends must be a Resource class, not {base!r}")
    parent = base.definition()
    if parent.discriminator is None:
        raise ConfigurationError(
            f"{owner} extends {parent.name!r}, which has no discriminator",
            hint='Set `discriminator = "type"` on the base resource.',
            doc="docs/polymorphism.md",
        )
    if parent.parent is not None:
        raise ConfigurationError(
            f"{owner}: {parent.name!r} is itself a subtype; one level of subtypes only",
            doc="docs/polymorphism.md",
        )
    if resource_cls.discriminator is not None:
        raise ConfigurationError(f"{owner}: a subtype has no discriminator of its own")
    if not (inspect.isclass(model) and issubclass(model, parent.model)):
        raise ConfigurationError(
            f"{owner}: the model {getattr(model, '__name__', model)!r} must subclass the "
            f"base model {parent.model.__name__!r}",
            hint="Subtype records are base records with more fields.",
            doc="docs/polymorphism.md",
        )
    return parent


def _inherited(
    owner: str, attr: str, resource_cls: type[Resource], parent: ResourceDefinition | None
) -> Any:
    """Tenancy and versioning are the base's: a subtype may repeat them, not change them."""
    own = getattr(resource_cls, attr)
    if parent is None:
        return own
    theirs = getattr(parent, attr)
    if own is not None and own != theirs:
        raise ConfigurationError(
            f"{owner}: {attr} {own!r} differs from the base's ({theirs!r})",
            hint="Subtypes share the base's records: leave it unset.",
            doc="docs/polymorphism.md",
        )
    return theirs


def _check_family(
    owner: str,
    resource_cls: type[Resource],
    parent: ResourceDefinition | None,
    schemas: Mapping[str, type[BaseModel]],
    operations: tuple[str, ...],
) -> str | None:
    """Rules of bases and subtypes; returns the discriminator of a base."""
    discriminator = resource_cls.discriminator
    read = schemas.get("read")
    if discriminator is not None:
        writes = [op for op in operations if op not in ("list", "get")]
        if writes:
            raise ConfigurationError(
                f"{owner}: a base resource only lists and reads; {', '.join(writes)} run on "
                "its subtypes",
                hint="Declare those operations on each subtype (extends = this resource).",
                doc="docs/polymorphism.md",
            )
        if read is None or discriminator not in read.model_fields:
            raise ConfigurationError(
                f"{owner}: the discriminator {discriminator!r} is not part of the read schema",
                doc="docs/polymorphism.md",
            )
        return discriminator
    if parent is None:
        return None
    base_read = parent.schemas.get("read")
    if read is not None and base_read is not None:
        missing = [f for f in base_read.model_fields if f not in read.model_fields]
        if missing:
            raise ConfigurationError(
                f"{owner}: the read schema lacks the base's fields {missing}",
                hint=f"Subclass {base_read.__name__} for the subtype's read schema.",
                doc="docs/polymorphism.md",
            )
    engine_owned = {
        parent.id_field: "id",
        parent.discriminator: "discriminator",
        parent.version_field: "version",
        **{f: "state" for f in parent.state_fields},
    }
    for role in ("create", "update"):
        schema = schemas.get(role)
        for fld, kind in engine_owned.items():
            if fld is not None and schema is not None and fld in schema.model_fields:
                raise ConfigurationError(
                    f"{owner}: the {kind} field {fld!r} is part of the {role!r} schema",
                    hint="The engine sets it.",
                    doc="docs/polymorphism.md",
                )
    return None


def _merge_hooks(
    base: Mapping[HookKey, tuple[Hook, ...]], own: Mapping[HookKey, tuple[Hook, ...]]
) -> dict[HookKey, tuple[Hook, ...]]:
    """A subtype runs the base's unqualified CRUD and commit hooks first, then its own."""
    shared = {
        key: hooks
        for key, hooks in base.items()
        if key.qualifier is None
        and key.point
        not in (
            HookPoint.BEFORE_TRANSITION,
            HookPoint.AFTER_TRANSITION,
            HookPoint.BEFORE_ACTION,
            HookPoint.AFTER_ACTION,
        )
    }
    merged = dict(shared)
    for key, hooks in own.items():
        merged[key] = (*merged.get(key, ()), *hooks)
    return merged


def _check_view_resource(
    owner: str,
    resource_cls: type[Resource],
    operations: tuple[str, ...],
    runners: Mapping[str, Any],
    actions: Mapping[str, Any],
    slots: Mapping[str, Any],
) -> None:
    """Views only list and read."""
    problems = [op for op in operations if op not in ("list", "get")]
    for what, present in (
        ("state machines", runners),
        ("actions", actions),
        ("document slots", slots),
        ("a version field", resource_cls.version_field),
        ("subtypes or a base", resource_cls.discriminator or resource_cls.extends),
        ("a link", resource_cls.link),
    ):
        if present:
            problems.append(what)
    if problems:
        raise ConfigurationError(
            f"{owner}: a view only lists and reads; remove {', '.join(problems)}",
            doc="docs/views.md",
        )


def _cache_ttl(owner: str, ttl: float | None) -> float | None:
    if ttl is not None and not ttl > 0:
        raise ConfigurationError(f"{owner}: cache_ttl must be positive seconds or None")
    return ttl
