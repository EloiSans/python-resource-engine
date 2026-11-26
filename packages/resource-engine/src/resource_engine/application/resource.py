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
from resource_engine.application.state_machines import MachineRunner
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
        link: On a join resource, the two reference fields it links
            (``("invoice_id", "tag_id")``): invoices get a ``tags`` relation and
            tags an ``invoices`` one (many-to-many).
        link_names: Names of those relations, by link field
            (``{"invoice_id": "labels"}``: the invoices' relation is ``labels``).
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
    link_names: ClassVar[Mapping[str, str]] = {}

    # --- Mapping between schemas and entities. Override when needed. ---

    @classmethod
    def build_entity(cls, data: BaseModel, ctx: OperationContext) -> Any:
        """Build a new entity from validated ``create`` input."""
        return cls.model(**data.model_dump())

    @classmethod
    def apply_update(cls, entity: Any, data: BaseModel, ctx: OperationContext) -> None:
        """Apply validated ``update`` input to an entity (only fields actually sent)."""
        protected = {
            cls.id_field,
            cls.state_field if cls.state_machine else None,
            cls.version_field,
        }
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
    dispatcher: HookDispatcher = field(repr=False, compare=False)
    machine: MachineRunner | None = field(repr=False, compare=False)

    def enabled(self, operation: str) -> bool:
        return operation in self.operations

    @property
    def transitions(self) -> tuple[str, ...]:
        return self.machine.info.events if self.machine else ()

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

        sm = resource_cls.state_machine
        state_field = resource_cls.state_field if sm else None
        if sm is not None:
            if not (inspect.isclass(sm) and issubclass(sm, StateMachine)):
                raise ConfigurationError(
                    f"{owner}: state_machine must be a python-statemachine StateMachine subclass"
                )
            for role in ("create", "update"):
                schema = schemas.get(role)
                if schema is not None and state_field in schema.model_fields:
                    raise StateFieldExposed(
                        f"{owner}: the state field {state_field!r} is part of the "
                        f"'{role}' schema {schema.__name__}",
                        hint="Remove it from the schema. State changes only through "
                        "transitions (POST /{id}/transitions/<event>).",
                        doc="docs/state-machines.md#protected-state",
                    )
        machine = MachineRunner(sm, state_field, name) if sm and state_field else None

        transition_schemas = dict(resource_cls.transition_schemas)
        if transition_schemas and machine is None:
            raise ConfigurationError(f"{owner} declares transition_schemas without a state_machine")
        for event in transition_schemas:
            if machine and event not in machine.info.events:
                raise ConfigurationError(
                    f"{owner}: transition_schemas references unknown transition {event!r}",
                    hint=f"Known transitions: {', '.join(machine.info.events)}",
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
        tenant_field = resource_cls.tenant_field
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
        version_field = resource_cls.version_field
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
        elif resource_cls.require_version:
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

        references = normalize_references(name, owner, resource_cls.references, known_fields)
        description = resource_cls.__doc__ and inspect.cleandoc(resource_cls.__doc__)
        return cls(
            name=name,
            resource_cls=resource_cls,
            model=model,
            id_field=resource_cls.id_field,
            id_type=resource_cls.id_type,
            schemas=schemas,
            operations=operations,
            operation_docs=dict(resource_cls.operation_docs),
            hooks=hooks,
            state_machine=sm,
            state_field=state_field,
            transition_schemas=transition_schemas,
            actions=actions,
            filterable=tuple(resource_cls.filterable),
            sortable=tuple(resource_cls.sortable),
            max_page_size=resource_cls.max_page_size,
            description=description or None,
            document_slots=slots,
            expose_transitions=resource_cls.expose_transitions,
            restricted_fields=tuple(resource_cls.restricted_fields),
            tenant_field=tenant_field,
            version_field=version_field,
            require_version=resource_cls.require_version,
            events=events,
            references=references,
            link=normalize_link(owner, resource_cls.link, resource_cls.link_names, references),
            link_names=dict(resource_cls.link_names),
            dispatcher=HookDispatcher(hooks),
            machine=machine,
        )
