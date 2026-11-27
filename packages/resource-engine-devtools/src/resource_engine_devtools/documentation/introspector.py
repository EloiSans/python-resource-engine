from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from typing import Any

import resource_engine
from pydantic import BaseModel
from resource_engine import ResourceDefinition, ResourceRegistry
from resource_engine.application.hooks import has_external_effect
from resource_engine.domain.events import event_doc

from resource_engine_devtools.documentation.docstrings import DocText, parse_doc
from resource_engine_devtools.documentation.model import (
    ActionDoc,
    DocumentationModel,
    EmailTemplateDoc,
    EmailUseDoc,
    EmailVariantDoc,
    EventDoc,
    FieldDoc,
    HookDoc,
    OperationDoc,
    ReactionDoc,
    ReferenceDoc,
    ResourceDoc,
    SchemaDoc,
    SlotDoc,
    StateDoc,
    StateMachineDoc,
    SystemRoleDoc,
    TimerDoc,
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
    """A type as it reads in Python 3.12+ source (``X | None``, ``Literal['a']``),
    the same on every Python version (``str()`` of typing objects is not)."""
    if annotation is type(None) or annotation is None:
        return "None"
    if annotation is Any:
        return "Any"
    if annotation is Ellipsis:
        return "..."
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        return _type_name(args[0])
    if origin in (typing.Union, types.UnionType):
        parts = [_type_name(a) for a in args if a is not type(None)]
        return " | ".join(parts + (["None"] if type(None) in args else []))
    if origin is typing.Literal:
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    if origin is not None:
        name = getattr(origin, "__name__", None) or str(origin).replace("typing.", "")
        return f"{name}[{', '.join(_type_name(a) for a in args)}]" if args else name
    if inspect.isclass(annotation):
        return annotation.__name__
    if isinstance(annotation, typing.ForwardRef):
        return annotation.__forward_arg__
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


def _event_doc(event: type) -> EventDoc:
    hints = typing.get_type_hints(event)
    fields = [
        FieldDoc(
            name=f.name,
            type=_type_name(hints.get(f.name, f.type)),
            required=f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING,
        )
        for f in dataclasses.fields(event)
        if f.name != "occurred_at"
    ]
    text = event_doc(event)
    return EventDoc(
        name=event.__name__,
        qualname=_qualname(event),
        doc=parse_doc(inspect.cleandoc(text)) if text else DocText(),
        fields=fields,
    )


def _reaction_doc(on: Any) -> ReactionDoc:
    when = [
        f"{label} {value}"
        for label, value in (
            ("resource", on.resource),
            ("transition", on.transition),
            ("action", on.action),
        )
        if value
    ]
    text = on.description or inspect.getdoc(on.handler)
    return ReactionDoc(
        event=on.event_type,
        when=", ".join(when),
        key=on.key if isinstance(on.key, str) else getattr(on.key, "__name__", "custom"),
        start=on.start,
        handler=getattr(on.handler, "__qualname__", repr(on.handler)),
        description=text.strip().split("\n\n")[0].replace("\n", " ") if text else None,
    )


def _reference_doc(ref: Any) -> ReferenceDoc:
    return ReferenceDoc(
        name=ref.name,
        field=ref.field,
        resource=ref.target if hasattr(ref, "target") else ref.resource,
        on_delete=ref.on_delete,
        description=ref.description,
    )


def _duration(value: Any) -> str:
    """``timedelta(days=30)`` → ``30d``; ``timedelta(hours=36)`` → ``1d 12h``."""
    seconds = int(value.total_seconds())
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return " ".join(parts) or "0s"


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

    machine_docs: list[StateMachineDoc] = []
    transition_text: dict[str, str] = {}
    for m in d.machines.values():
        machine_text = parse_doc(m.machine_cls)
        state_text = machine_text.items.get("states", {})
        own_text = machine_text.items.get("transitions", {})
        transition_text.update({e: t for e, t in own_text.items() if e in m.info.events})
        machine_docs.append(
            StateMachineDoc(
                name=m.machine_cls.__name__,
                qualname=_qualname(m.machine_cls),
                state_field=m.state_field,
                doc=machine_text,
                states=[
                    StateDoc(s.id, s.name, s.initial, s.final, state_text.get(s.id))
                    for s in m.info.states
                ],
                transitions=[
                    TransitionDoc(t.event, t.source, t.target, own_text.get(t.event))
                    for t in m.info.transitions
                ],
                timers=[
                    TimerDoc(
                        event=t.event,
                        after=_duration(t.after),
                        since=t.since,
                        states=[str(s) for s in t.states],
                        description=t.description,
                    )
                    for t in d.timers
                    if t.event in m.info.events
                ],
                since_field=m.since_field,
                machine=m.name,
            )
        )
    if d.machine is not None:
        for event in d.transitions:
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
        state_machines=machine_docs,
        discriminator=d.discriminator,
        extends=d.parent.name if d.parent is not None else None,
        identity=d.identity,
        **_view_doc(d.view),
        filterable=list(d.filterable),
        sortable=list(d.sortable),
        restricted_fields=list(d.restricted_fields),
        tenant_field=d.tenant_field,
        version_field=d.version_field,
        require_version=d.require_version,
        audit=getattr(d.resource_cls, "audit", True),
        audit_reads=getattr(d.resource_cls, "audit_reads", False),
        audit_exclude=list(getattr(d.resource_cls, "audit_exclude", ())),
        events=[_event_doc(e) for e in d.events],
        references=[_reference_doc(r) for r in d.references.values()],
        correlation=getattr(d.resource_cls, "correlation", None)
        if getattr(d.resource_cls, "reacts", None)
        else None,
        reacts=[_reaction_doc(on) for on in getattr(d.resource_cls, "reacts", ())],
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


def introspect(
    registry: ResourceRegistry,
    authz: Any = None,
    *,
    templates: Any = None,
    email_prefix: str = "email/",
) -> DocumentationModel:
    """``authz``: an ``AuthzConfig`` to document system roles (permission matrix).
    ``templates``: the application's ``TemplateRenderer``, to show the emails."""
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
    resources = [_resource_doc(d) for d in registry]
    by_name = {r.name: r for r in resources}
    for d in registry:
        if d.parent is not None and d.parent.name in by_name and d.identity:
            by_name[d.parent.name].subtypes[d.identity] = d.name
    by_name = {r.name: r for r in resources}
    for d in registry:
        for ref in d.references.values():
            if ref.target in by_name:
                by_name[ref.target].referenced_by.append(
                    ReferenceDoc(
                        name=ref.name,
                        field=f"{d.name}.{ref.field}",
                        resource=d.name,
                        on_delete=ref.on_delete,
                        description=ref.description,
                        related_name=ref.related_name if d.enabled("list") else None,
                    )
                )
    for d in registry:
        if d.link is None:
            continue
        by_field = {r.field: r for r in d.references.values()}
        a, b = (by_field[f] for f in d.link)
        for source, target in ((a, b), (b, a)):
            if source.target in by_name:
                by_name[source.target].links.append(
                    ReferenceDoc(
                        name=d.link_names.get(source.field, target.target),
                        field=d.name,
                        resource=target.target,
                        on_delete="link",
                    )
                )
    emails = _emails(registry, by_name, templates, email_prefix)
    return DocumentationModel(
        resources=resources,
        engine_version=resource_engine.__version__,
        actions=documented,
        audit_resource="audit_log" if "audit_log" in registry else None,
        system_roles=roles,
        emails=emails,
    )


def _emails(
    registry: ResourceRegistry, by_name: dict[str, ResourceDoc], templates: Any, prefix: str
) -> list[EmailTemplateDoc]:
    """Each resource's requests, and every template set with a preview per locale."""
    from resource_engine_devtools.emails import find_email_requests
    from resource_engine_devtools.templates_check import missing_template

    used: dict[str, list[str]] = {}
    missing: dict[str, set[str]] = {}
    seen: set[tuple[str, str, str | None]] = set()
    for use in find_email_requests(registry):
        resource = by_name.get(use.resource)
        if resource is None or (use.point, use.function, use.template) in seen:
            continue
        seen.add((use.point, use.function, use.template))
        resource.emails.append(
            EmailUseDoc(use.template, use.point, use.function, use.locale, use.to)
        )
        if use.template is None:
            continue
        used.setdefault(use.template, []).append(f"{use.resource} {use.point}")
        if templates is not None:
            gap = missing_template(templates, prefix, use.template, use.locale)
            if gap:
                missing.setdefault(use.template, set()).add(gap)
    names = set(used)
    if templates is not None:
        suffix = ".subject.txt"
        names |= {
            n[len(prefix) : -len(suffix)]
            for n in templates.names()
            if n.startswith(prefix) and n.endswith(suffix)
        }
    out = []
    for name in sorted(names):
        doc = EmailTemplateDoc(name, used.get(name, []), missing=sorted(missing.get(name, ())))
        if templates is not None and hasattr(templates, "preview"):
            doc.variants = _variants(templates, f"{prefix}{name}")
        out.append(doc)
    return out


def _variants(templates: Any, base: str) -> list[EmailVariantDoc]:
    from resource_engine.errors import TemplateFailed, TemplateNotFound

    files = (f"{base}.subject.txt", f"{base}.txt", f"{base}.html")
    locales: list[str | None] = []
    for name in files:
        for locale in templates.variants(name):
            if locale not in locales:
                locales.append(locale)
    default = getattr(templates, "default_locale", None)
    # the default locale first, the unlocalised fallback last
    locales.sort(key=lambda loc: (loc != default, loc is None, loc or ""))

    def show(name: str, locale: str | None) -> str | None:
        try:
            return templates.preview(name, locale=locale)
        except TemplateNotFound:
            return None
        except TemplateFailed as exc:
            return f"⚠ {exc.message}"

    return [
        EmailVariantDoc(
            locale,
            " ".join((show(files[0], locale) or "").split()) or None,
            show(files[1], locale),
            show(files[2], locale),
        )
        for locale in locales
    ]


def _view_doc(view: Any) -> dict[str, Any]:
    from resource_engine.application.views import Aggregate

    if view is None:
        return {}
    if not isinstance(view, Aggregate):
        return {"view": "custom"}
    values = {}
    for name, m in view.values.items():
        text = f"{m.kind}({m.field or ''})"
        if m.where:
            text += " where " + ", ".join(f"{k}={v}" for k, v in m.where.items())
        values[name] = text
    return {"view": view.source, "view_group_by": list(view.group_by), "view_values": values}
