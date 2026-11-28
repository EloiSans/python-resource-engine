"""MkDocs-compatible Markdown. One page per resource plus an index.

The generator's texts are English and go through ``t()``: render inside
``docs_locale("ca")`` for another language (see ``documentation.i18n``).
"""

from __future__ import annotations

import re
from typing import Any

from resource_engine_devtools.documentation.docstrings import FUNCTIONAL_SECTIONS, DocText
from resource_engine_devtools.documentation.generators.mermaid import render_state_machine
from resource_engine_devtools.documentation.i18n import t
from resource_engine_devtools.documentation.model import DocumentationModel, ResourceDoc

_TITLES = {key: title for title, key in FUNCTIONAL_SECTIONS.items()}


def _undocumented() -> str:
    return t("_No description._")


def _yes(value: bool) -> str:
    return t("yes") if value else t("no")


def _on_delete(kind: str) -> str:
    return {
        "restrict": t("refused while referenced"),
        "set_null": t("the field is emptied"),
        "cascade": t("deleted in cascade"),
    }[kind]


def _text(doc: DocText, *, heading: str = "###") -> list[str]:
    out: list[str] = []
    if doc.summary:
        out.append(doc.summary)
    if doc.description:
        out += ["", doc.description]
    for key, body in doc.sections.items():
        out += ["", f"{heading} {t(_TITLES.get(key, key))}", "", body]
    return out or [_undocumented()]


def _cell(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _codes(names: Any) -> str:
    return ", ".join(f"`{n}`" for n in names)


def render_resource(r: ResourceDoc, audit_resource: str | None = None) -> str:
    out = [f"# {r.name}", ""]
    out += _text(r.doc, heading="##")

    out += [
        "",
        f"## {t('Operations')}",
        "",
        f"| {t('Operation')} | {t('Kind')} | {t('Description')} |",
        "|---|---|---|",
    ]
    for op in r.operations:
        out.append(f"| `{op.name}` | {op.kind} | {_cell(op.doc.summary) or '—'} |")
    if r.view == "custom":
        out += ["", t("Read-only view: the records are computed by a query.")]
    elif r.view:
        out += [
            "",
            t(
                "Read-only view computed from [{source}]({source}.md), one row per {groups}:",
                source=r.view,
                groups=_codes(r.view_group_by),
            ),
            "",
            f"| {t('Field')} | {t('Computation')} |",
            "|---|---|",
            *(f"| `{k}` | `{v}` |" for k, v in r.view_values.items()),
        ]
    if r.discriminator:
        kinds = ", ".join(f"`{i}` ([{n}]({n}.md))" for i, n in r.subtypes.items()) or "—"
        out += [
            "",
            t(
                "Base resource: the `{field}` field holds each record's subtype ({kinds}). It "
                "lists and reads every kind; each subtype creates, updates and deletes its own.",
                field=r.discriminator,
                kinds=kinds,
            ),
        ]
    if r.extends:
        out += [
            "",
            t(
                "Subtype of [{base}]({base}.md) (`{identity}`): it shares its ids, common "
                "fields, state machines and hooks.",
                base=r.extends,
                identity=r.identity,
            ),
        ]
    if r.tenant_field:
        out += [
            "",
            t(
                "Multi-tenant: each record belongs to the tenant in `{field}`; operations are "
                "limited to the caller's tenant.",
                field=r.tenant_field,
            ),
        ]
    if r.restricted_fields:
        out += [
            "",
            t(
                "Restricted fields: {fields} — only roles with an explicit grant see or "
                "change them.",
                fields=_codes(r.restricted_fields),
            ),
        ]
    if audit_resource and r.name != audit_resource:
        if not r.audit:
            note = t("not audited.")
        else:
            hidden = sorted({*r.restricted_fields, *r.audit_exclude})
            note = (
                t("changes and refused attempts are audited, reads too")
                if r.audit_reads
                else t("changes and refused attempts are audited")
            )
            if hidden:
                note += t("; without values for {fields}", fields=_codes(hidden))
            note += "."
        out += ["", t("Audit: {note}", note=note)]

    several = len(r.state_machines) > 1
    for sm in r.state_machines:
        title = t("State machine `{name}`", name=sm.machine) if several else t("State machine")
        out += ["", f"## {title}", ""]
        out += _text(sm.doc)
        out += [
            "",
            "```mermaid",
            render_state_machine(sm),
            "```",
            "",
            f"| {t('State')} | {t('Name')} | {t('Description')} |",
            "|---|---|---|",
        ]
        for s in sm.states:
            flags = f" ({t('initial')})" if s.initial else f" ({t('final')})" if s.final else ""
            out.append(f"| `{s.id}`{flags} | {s.name} | {_cell(s.description) or '—'} |")
        out += [
            "",
            f"| {t('Transition')} | {t('From')} | {t('To')} | {t('Description')} |",
            "|---|---|---|---|",
        ]
        for tr in sm.transitions:
            out.append(
                f"| `{tr.event}` | `{tr.source}` | `{tr.target}` | {_cell(tr.description) or '—'} |"
            )

    timers = [timer for sm in r.state_machines for timer in sm.timers]
    if timers:
        out += [
            "",
            f"### {t('Automatic transitions')}",
            "",
            f"| {t('Transition')} | {t('After')} | {t('From')} | {t('Counted from')} "
            f"| {t('Description')} |",
            "|---|---|---|---|---|",
        ]
        for timer in timers:
            out.append(
                f"| `{timer.event}` | {timer.after} | {_codes(timer.states)} | `{timer.since}` "
                f"| {_cell(timer.description) or '—'} |"
            )
        out += [
            "",
            t(
                "The worker (`TimerJob`) runs them as the system principal; they appear in "
                "the history and the audit trail like any transition."
            ),
        ]

    if r.actions:
        out += ["", f"## {t('Actions')}"]
        for a in r.actions:
            out += ["", f"### `{a.name}`", ""]
            out += _text(a.doc, heading="####")

    if r.reacts:
        out += [
            "",
            f"## {t('Process')}",
            "",
            t(
                "Each record is an instance of the process, one per value of `{field}`. It "
                "reacts to these events:",
                field=r.correlation,
            ),
            "",
            f"| {t('Event')} | {t('When')} | {t('Key')} | {t('Starts')} | {t('What it does')} |",
            "|---|---|---|---|---|",
        ]
        for x in r.reacts:
            out.append(
                f"| `{x.event}` | {x.when or '—'} | `{x.key}` | {_yes(x.start)} "
                f"| {_cell(x.description) or x.handler} |"
            )

    if r.references:
        out += [
            "",
            f"## {t('References')}",
            "",
            f"| {t('Relation')} | {t('Field')} | {t('Resource')} | {t('When it is deleted')} "
            f"| {t('Description')} |",
            "|---|---|---|---|---|",
        ]
        for ref in r.references:
            out.append(
                f"| `{ref.name}` | `{ref.field}` | [{ref.resource}]({ref.resource}.md) "
                f"| {_on_delete(ref.on_delete)} | {_cell(ref.description) or '—'} |"
            )
    if r.referenced_by:
        refs = ", ".join(
            f"[{ref.field}]({ref.resource}.md) ({_on_delete(ref.on_delete)})"
            for ref in r.referenced_by
        )
        out += ["", t("Referenced by: {refs}.", refs=refs)]
    if r.links:
        out += [
            "",
            f"## {t('Many-to-many relations')}",
            "",
            f"| {t('Relation')} | {t('Records')} | {t('Through')} |",
            "|---|---|---|",
        ]
        for link in r.links:
            out.append(
                f"| `{link.name}` | [{link.resource}]({link.resource}.md) "
                f"| [{link.field}]({link.field}.md) |"
            )
        out += [
            "",
            t(
                "They are linked and unlinked by creating and deleting records of the join "
                "resource; `expand` accepts these relations too."
            ),
        ]

    if r.events:
        out += ["", f"## {t('Domain events')}"]
        for e in r.events:
            out += ["", f"### `{e.name}`", ""]
            out += _text(e.doc, heading="####")
            out += [
                "",
                f"| {t('Field')} | {t('Type')} | {t('Required')} |",
                "|---|---|---|",
            ]
            for f in e.fields:
                out.append(f"| `{f.name}` | `{_cell(f.type)}` | {_yes(f.required)} |")

    if r.documents:
        out += [
            "",
            f"## {t('Documents')}",
            "",
            f"| {t('Slot')} | {t('Types')} | {t('Max. size')} | {t('Multiple')} | {t('Upload')} "
            f"| {t('Required by')} | {t('Processing')} | {t('Description')} |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for slot in r.documents:
            out.append(
                f"| `{slot.name}` | {', '.join(slot.content_types)} | {slot.max_bytes} B "
                f"| {_yes(slot.multiple)} | {slot.upload} | {_codes(slot.required_for) or '—'} "
                f"| {_yes(slot.processing)} | {_cell(slot.description) or '—'} |"
            )

    if r.emails:
        out += [
            "",
            f"## {t('Emails')}",
            "",
            f"| {t('Email')} | {t('When')} | {t('Recipients')} | {t('Language')} |",
            "|---|---|---|---|",
        ]
        for e in r.emails:
            name = (
                f"[`{e.template}`](../emails.md#{_slug(e.template)})"
                if e.template
                else t("_computed at runtime_")
            )
            when = _point_label(e.point)
            out.append(f"| {name} | {when} | `{_cell(e.to)}` | {e.locale or '—'} |")
        out += [
            "",
            t(
                "They are requested inside the transaction and sent once the change is "
                "committed; if the change is rolled back, they are not sent."
            ),
        ]

    out += _lifecycle(r)

    return "\n".join(out) + "\n"


# ------------------------------------------------- what happens in each operation


def _point_label(point: str) -> str:
    """``after_transition:approve`` → "on the `approve` transition"."""
    if point.startswith("action:"):
        return t("on the `{name}` action", name=point[7:])
    base, _, q = point.partition(":")
    kind = base.split("_", 1)[1] if "_" in base else base
    if kind == "transition":
        return t("on the `{name}` transition", name=q) if q else t("on any transition")
    if kind == "action":
        return t("on the `{name}` action", name=q) if q else t("on any action")
    if kind == "attach":
        return t("when attaching to `{name}`", name=q) if q else t("when attaching a document")
    if kind == "detach":
        return t("when removing from `{name}`", name=q) if q else t("when removing a document")
    labels = {
        "create": t("on create"),
        "update": t("on update"),
        "delete": t("on delete"),
        "validate": t("when validating the input"),
        "commit": t("after any committed change"),
    }
    return labels.get(kind, f"`{point}`")


def _steps_for(r: ResourceDoc) -> list[tuple[str, str, str | None, str | None]]:
    """``(kind, title, qualifier, input schema)`` of every write operation."""
    titles = {"create": t("Create"), "update": t("Update"), "delete": t("Delete")}
    out = []
    for op in r.operations:
        if op.kind == "crud" and op.name in titles:
            out.append((op.name, f"{titles[op.name]} (`{op.name}`)", None, op.input_schema))
        elif op.kind == "transition":
            title = t("Transition `{name}`", name=op.name)
            out.append(("transition", title, op.name, op.input_schema))
        elif op.kind == "action":
            out.append(("action", t("Action `{name}`", name=op.name), op.name, op.input_schema))
    for slot in r.documents:
        out.append(("attach", t("Attach to `{name}`", name=slot.name), slot.name, None))
        out.append(("detach", t("Remove from `{name}`", name=slot.name), slot.name, None))
    return out


def _hooks_at(r: ResourceDoc, point: str, qualifier: str | None) -> list[Any]:
    """Generic hooks first, then the qualified ones (the engine's order)."""
    generic = [h for h in r.hooks if h.point == point]
    qualified = [h for h in r.hooks if qualifier and h.point == f"{point}:{qualifier}"]
    return generic + qualified


def _mutation(kind: str) -> tuple[str, str]:
    return {
        "create": (t("The new record is saved."), t("Save")),
        "update": (t("The changes are saved."), t("Save")),
        "delete": (t("The record is deleted."), t("Delete")),
        "transition": (t("The state changes."), t("Change the state")),
        "action": (t("The action runs."), t("Run")),
        "attach": (t("The document is saved."), t("Save the document")),
        "detach": (t("The document is removed."), t("Remove the document")),
    }.get(kind, (t("The change is saved."), t("Save")))


def _commit() -> str:
    return t("Commit")


def _lifecycle(r: ResourceDoc) -> list[str]:
    """What runs in each write operation, in the order of docs/lifecycle.md."""
    if not r.hooks and not r.emails:
        return []
    out = [
        "",
        f"## {t('What happens in each operation')}",
        "",
        t(
            "Steps **before the commit** are inside the transaction: if one fails, the change "
            "is refused and nothing is saved. Steps **after the commit** run with the change "
            "already saved: an error is logged, but it doesn't undo the change."
        ),
    ]
    for kind, title, qualifier, _schema in _steps_for(r):
        validates = kind in ("create", "update", "transition", "action")
        before = _hooks_at(r, f"before_{kind}", qualifier)
        after = _hooks_at(r, f"after_{kind}", qualifier)
        checks = (
            _hooks_at(r, "before_validate", None) + _hooks_at(r, "after_validate", None)
            if validates
            else []
        )
        point_key = f"action:{qualifier}" if kind == "action" else None
        mails = [
            e
            for e in r.emails
            if e.point in {h.point for h in before + after + checks}
            or (point_key and e.point == point_key)
        ]
        required = [s.name for s in r.documents if qualifier and qualifier in s.required_for]
        if not (before or after or checks or mails or required):
            continue
        # (sentence for the list, short label for the diagram)
        steps: list[tuple[str, str]] = []
        if validates:
            text = t("The input is validated")
            steps.append(
                (text + "".join(f"; {_hook_text(h)}" for h in checks) + ".", t("Validate"))
            )
        if required:
            steps.append((
                t("A document must be available in {slots}.", slots=_codes(required)),
                t("Document {slots}", slots=", ".join(required)),
            ))  # fmt: skip
        steps += [(t("Before saving: {hook}", hook=_hook_text(h)), _hook_label(h)) for h in before]
        steps.append(_mutation(kind))
        steps += [
            (t("Before the commit: {hook}", hook=_hook_text(h)), _hook_label(h)) for h in after
        ]
        committed = t("The change is committed, with the audit entry and the events.")
        steps.append((committed, _commit()))
        for e in mails:
            link = f"[`{e.template}`](../emails.md#{_slug(e.template)})" if e.template else ""
            sent = t("After the commit: the email {email} is sent to `{to}`.", email=link, to=e.to)
            steps.append((sent, t("Email {name}", name=e.template or "").strip()))
        out += ["", f"### {title}", ""]
        out += [f"{i}. {text}" for i, (text, _) in enumerate(steps, 1)]
        out += ["", "```mermaid", _flow([label for _, label in steps]), "```"]
    commit = [h for h in r.hooks if h.point == "after_commit"]
    if commit:
        out += ["", t("After any committed change of this resource:"), ""]
        out += [f"- {_hook_text(h)}" for h in commit]
    return out


def _hook_label(h: Any) -> str:
    """The hook in a diagram: the start of what it does."""
    summary = (h.doc.summary or h.name).rstrip(".")
    return summary if len(summary) <= 40 else summary[:39].rstrip() + "…"


def _hook_text(h: Any) -> str:
    effect = f" ({t('external effect')})" if h.external_effect else ""
    summary = _cell(h.doc.summary) or t("no description")
    return f"{summary}{effect}"  # what it does, not the function's name


def _flow(labels: list[str]) -> str:
    """The steps as a left-to-right flowchart; the commit is a hexagon."""
    commit = _commit()
    lines = ["flowchart LR"]
    for i, label in enumerate(labels):
        label = label.replace('"', "'")
        shape = ('{{"', '"}}') if label == commit else ('["', '"]')
        lines.append(f"    s{i}{shape[0]}{label}{shape[1]}")
        if i:
            lines.append(f"    s{i - 1} --> s{i}")
    return "\n".join(lines)


def _slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")


# ------------------------------------------------------------------ pages


def render_emails(model: DocumentationModel) -> str:
    """Every email template set, with a preview per locale."""
    out = [
        f"# {t('Emails')}",
        "",
        t(
            "The emails the application sends, from its templates. Variables show as "
            "`{{ name }}`: the real content depends on each case."
        ),
    ]
    for e in model.emails:
        out += ["", f"## {e.template}", ""]
        if e.used_by:
            sent = "; ".join(
                t(
                    "by [{resource}](resources/{resource}.md) {when}",
                    resource=u.split(" ", 1)[0],
                    when=_point_label(u.split(" ", 1)[1]),
                )
                for u in e.used_by
            )
            out.append(t("Sent {by}.", by=sent))
        else:
            out.append(t("No resource requests it: it is sent directly (`Mailer.send`)."))
        if e.missing:
            out += ["", "⚠ " + t("Missing templates: {names}.", names=_codes(e.missing))]
        for v in e.variants:
            label = v.locale or t("Other languages (not translated)")
            out += ["", f"### {label}", ""]
            out += [f"**{t('Subject:')}** {_cell(v.subject) or '—'}"]
            if v.text:
                out += ["", "```text", v.text.rstrip("\n"), "```"]
            if v.html:
                out += ["", "```email-html", v.html.rstrip("\n"), "```"]
    return "\n".join(out) + "\n"


def render_markdown(model: DocumentationModel) -> dict[str, str]:
    """Return ``{relative_path: content}``."""
    pages = {
        f"resources/{r.name}.md": render_resource(r, model.audit_resource) for r in model.resources
    }
    index = [
        f"# {t('Resource catalogue')}",
        "",
        f"| {t('Resource')} | {t('Description')} | {t('Operations')} | {t('State machines')} |",
        "|---|---|---|---|",
    ]
    for r in model.resources:
        ops = _codes(o.name for o in r.operations)
        machines = ", ".join(
            f"[{sm.machine or sm.state_field}](state-machines.md#{_anchor(r.name, sm)})"
            for sm in r.state_machines
        )
        index.append(
            f"| [{r.name}](resources/{r.name}.md) | {_cell(r.doc.summary) or '—'} | {ops} "
            f"| {machines or '—'} |"
        )
    if any(r.state_machines for r in model.resources):
        index += ["", t("Every state diagram: [state machines](state-machines.md).")]
        pages["state-machines.md"] = render_state_machines(model)
    if model.emails:
        index += ["", t("The emails the application sends: [emails](emails.md).")]
        pages["emails.md"] = render_emails(model)
    if model.system_roles:
        index += ["", t("See also the [permission matrix](permissions.md).")]
        pages["permissions.md"] = render_permissions(model)
    pages["index.md"] = "\n".join(index) + "\n"
    return pages


def render_permissions(model: DocumentationModel) -> str:
    """Matrix of system roles × actions. Policies created from the front end are
    data and are listed by the authz_policies resource, not here."""
    roles = model.system_roles
    out = [
        f"# {t('Permission matrix (system roles)')}",
        "",
        t(
            "System roles are defined in the code and can't be changed from the front end. "
            "Policies created from the front end are listed by `authz_policies`."
        ),
        "",
        f"| {t('Role')} | {t('Description')} | {t('Grants')} |",
        "|---|---|---|",
    ]
    for role in roles:
        out.append(f"| `{role.name}` | {_cell(role.description) or '—'} | {_codes(role.grants)} |")
    out += ["", f"| {t('Action')} | " + " | ".join(f"`{role.name}`" for role in roles) + " |"]
    out.append("|---|" + "---|" * len(roles))
    for action in model.actions:
        marks = " | ".join("✓" if action in role.actions else "" for role in roles)
        out.append(f"| `{action}` | {marks} |")
    return "\n".join(out) + "\n"


def _anchor(resource: str, sm: Any) -> str:
    """The heading id of a machine on the state-machines page (``<h2>`` slug)."""
    # the same slug on GitHub and on the site: "invoices: approval" → invoices-approval
    text = f"{resource}: {sm.machine or sm.state_field}".lower()
    return re.sub(r"[^\w]+", "-", text).strip("-")


def render_state_machines(model: DocumentationModel) -> str:
    """Every state machine of the application, with its diagram."""
    out = [
        f"# {t('State machines')}",
        "",
        t(
            "The processes with states of each resource, drawn from the executable "
            "definition of the machine. Each state and transition is described on the "
            "resource's page."
        ),
    ]
    for r in model.resources:
        for sm in r.state_machines:
            where = t("Resource [{name}](resources/{name}.md)", name=r.name)
            out += ["", f"## {r.name}: {sm.machine or sm.state_field}", "", where, ""]
            if sm.doc.summary:
                out += [sm.doc.summary, ""]
            out += ["```mermaid", render_state_machine(sm), "```"]
            if sm.timers:
                timers = ", ".join(
                    t("`{event}` after {after}", event=timer.event, after=timer.after)
                    for timer in sm.timers
                )
                out += ["", t("Automatic transitions: {timers}.", timers=timers)]
    return "\n".join(out) + "\n"
