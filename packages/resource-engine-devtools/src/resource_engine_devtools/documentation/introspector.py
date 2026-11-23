from __future__ import annotations

import inspect
from typing import Any

import resource_engine
from pydantic import BaseModel
from resource_engine import ResourceDefinition, ResourceRegistry
from resource_engine.application.hooks import has_external_effect

from resource_engine_devtools.documentation.docstrings import DocText, parse_doc
from resource_engine_devtools.documentation.model import (
    ActionDoc,
    DocumentationModel,
    FieldDoc,
    HookDoc,
    OperationDoc,
    ResourceDoc,
    SchemaDoc,
    SlotDoc,
    StateDoc,
    StateMachineDoc,
    SystemRoleDoc,
    TransitionDoc,
)

_CRUD_HTTP = {
    "list": ("GET", ""),
    "get": ("GET", "/{id}"),
    "create": ("POST", ""),
    "update": ("PATCH", "/{id}"),
    "delete": ("DELETE", "/{id}"),
}
_CRUD_SCHEMAS = {
    "list": (None, "read"),
    "get": (None, "read"),
    "create": ("create", "read"),
    "update": ("update", "read"),
    "delete": (None, None),
}


def _type_name(annotation: Any) -> str:
    if inspect.isclass(annotation):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def _schema_doc(role: str, schema: type[BaseModel]) -> SchemaDoc:
    fields = [
        FieldDoc(
            name=name,
            type=_type_name(info.annotation),
            required=info.is_required(),
            description=info.description,
        )
        for name, info in schema.model_fields.items()
    ]
    return SchemaDoc(name=schema.__name__, role=role, fields=fields, doc=parse_doc(schema))


def _qualname(obj: Any) -> str:
    return f"{getattr(obj, '__module__', '?')}.{getattr(obj, '__qualname__', repr(obj))}"


def _resource_doc(d: ResourceDefinition) -> ResourceDoc:
    base = f"/{d.name}"
    operations: list[OperationDoc] = []
    for op in d.operations:
        method, suffix = _CRUD_HTTP[op]
        inp, out = _CRUD_SCHEMAS[op]
        operations.append(
            OperationDoc(
                name=op,
                kind="crud",
                method=method,
                path=base + suffix,
                doc=parse_doc(d.operation_docs[op]) if op in d.operation_docs else DocText(),
                input_schema=d.schemas[inp].__name__ if inp else None,
                output_schema=d.schemas[out].__name__ if out else None,
            )
        )

    sm_doc = None
    if d.machine is not None and d.state_machine is not None:
        machine_text = parse_doc(d.state_machine)
        state_text = machine_text.items.get("states", {})
        transition_text = machine_text.items.get("transitions", {})
        sm_doc = StateMachineDoc(
            name=d.state_machine.__name__,
            qualname=_qualname(d.state_machine),
            state_field=d.state_field or "",
            doc=machine_text,
            states=[
                StateDoc(s.id, s.name, s.initial, s.final, state_text.get(s.id))
                for s in d.machine.info.states
            ],
            transitions=[
                TransitionDoc(t.event, t.source, t.target, transition_text.get(t.event))
                for t in d.machine.info.transitions
            ],
        )
        for event in d.machine.info.events:
            schema = d.transition_schemas.get(event)
            text = transition_text.get(event)
            operations.append(
                OperationDoc(
                    name=event,
                    kind="transition",
                    method="POST",
                    path=f"{base}/{{id}}/transitions/{event}",
                    doc=parse_doc(text) if text else DocText(),
                    input_schema=schema.__name__ if schema else None,
                    output_schema=d.schemas["read"].__name__ if "read" in d.schemas else None,
                )
            )

    actions: list[ActionDoc] = []
    for a in d.actions.values():
        doc = parse_doc(a.action_cls)
        actions.append(
            ActionDoc(
                name=a.name,
                qualname=_qualname(a.action_cls),
                instance=a.instance,
                doc=doc,
                input_schema=a.input_schema.__name__ if a.input_schema else None,
                output_schema=a.output_schema.__name__ if a.output_schema else None,
            )
        )
        operations.append(
            OperationDoc(
                name=a.name,
                kind="action",
                method="POST",
                path=f"{base}/{{id}}/actions/{a.name}"
                if a.instance
                else f"{base}/actions/{a.name}",
                doc=doc,
                input_schema=a.input_schema.__name__ if a.input_schema else None,
                output_schema=a.output_schema.__name__ if a.output_schema else None,
            )
        )

    hooks = [
        HookDoc(
            point=str(key),
            name=getattr(fn, "__name__", repr(fn)),
            qualname=_qualname(fn),
            external_effect=has_external_effect(fn),
            doc=parse_doc(fn),
        )
        for key, fns in d.hooks.items()
        for fn in fns
    ]

    return ResourceDoc(
        name=d.name,
        qualname=_qualname(d.resource_cls),
        module=d.resource_cls.__module__,
        model=d.model.__name__,
        doc=parse_doc(d.resource_cls),
        operations=operations,
        schemas=[_schema_doc(role, s) for role, s in d.schemas.items()],
        hooks=hooks,
        actions=actions,
        state_machine=sm_doc,
        filterable=list(d.filterable),
        sortable=list(d.sortable),
        restricted_fields=list(d.restricted_fields),
        audit=getattr(d.resource_cls, "audit", True),
        audit_reads=getattr(d.resource_cls, "audit_reads", False),
        audit_exclude=list(getattr(d.resource_cls, "audit_exclude", ())),
        documents=[
            SlotDoc(
                name=slot.name,
                description=slot.description,
                content_types=list(slot.content_types),
                max_bytes=slot.max_bytes,
                multiple=slot.multiple,
                upload=slot.upload,
                required_for=list(slot.required_for),
                processing=slot.processing,
            )
            for slot in d.document_slots.values()
        ],
    )


def introspect(registry: ResourceRegistry, authz: Any = None) -> DocumentationModel:
    """``authz``: an ``AuthzConfig`` to document system roles (permission matrix)."""
    from resource_engine.authz.catalog import FIELD_KINDS, Catalog
    from resource_engine.authz.evaluator import names_field

    catalog = Catalog.from_registry(registry)
    # open fields follow their record: only restricted ones are worth a row
    documented = [
        name
        for name, info in catalog.actions.items()
        if info.kind not in FIELD_KINDS or info.restricted
    ]
    roles = []
    for role in getattr(authz, "system_roles", ()):
        covered = sorted(
            {
                a
                for g in role.grants
                for a in catalog.matching(g, include_sensitive=role.include_sensitive)
                if a in documented
                and (not catalog.actions[a].restricted or role.include_sensitive or names_field(g))
            }
        )
        roles.append(SystemRoleDoc(role.name, role.description, list(role.grants), covered))
    return DocumentationModel(
        resources=[_resource_doc(d) for d in registry],
        engine_version=resource_engine.__version__,
        actions=documented,
        audit_resource="audit_log" if "audit_log" in registry else None,
        system_roles=roles,
    )
