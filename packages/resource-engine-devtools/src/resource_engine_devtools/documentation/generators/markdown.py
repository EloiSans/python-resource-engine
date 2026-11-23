"""MkDocs-compatible Markdown. One page per resource plus an index."""

from __future__ import annotations

from resource_engine_devtools.documentation.docstrings import FUNCTIONAL_SECTIONS, DocText
from resource_engine_devtools.documentation.generators.mermaid import render_state_machine
from resource_engine_devtools.documentation.model import DocumentationModel, ResourceDoc

_TITLES = {key: title for title, key in FUNCTIONAL_SECTIONS.items()}
UNDOCUMENTED = "_Sense descripció._"


def _text(doc: DocText, *, heading: str = "###") -> list[str]:
    out: list[str] = []
    if doc.summary:
        out.append(doc.summary)
    if doc.description:
        out += ["", doc.description]
    for key, body in doc.sections.items():
        out += ["", f"{heading} {_TITLES.get(key, key)}", "", body]
    return out or [UNDOCUMENTED]


def _cell(value: str | None) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def render_resource(r: ResourceDoc) -> str:
    out = [f"# {r.name}", "", f"`{r.qualname}` — entitat `{r.model}`", ""]
    out += _text(r.doc, heading="##")

    out += [
        "",
        "## Operacions",
        "",
        "| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |",
        "|---|---|---|---|---|---|",
    ]
    for op in r.operations:
        out.append(
            f"| `{op.name}` | {op.kind} | `{op.method} {op.path}` | {op.input_schema or ''} "
            f"| {op.output_schema or ''} | {_cell(op.doc.summary) or '—'} |"
        )
    if r.filterable or r.sortable:
        out += [
            "",
            f"Filtres: {', '.join(f'`{f}`' for f in r.filterable) or '—'} · "
            f"Ordenació: {', '.join(f'`{f}`' for f in r.sortable) or '—'}",
        ]
    if r.restricted_fields:
        out += [
            "",
            "Camps restringits: "
            + ", ".join(f"`{f}`" for f in r.restricted_fields)
            + f" — cal una concessió explícita (`{r.name}:read:<camp>`, "
            f"`{r.name}:write:<camp>`); si no, s'ometen de les respostes.",
        ]

    if r.state_machine:
        sm = r.state_machine
        out += ["", "## Màquina d'estats", "", f"`{sm.qualname}` — camp `{sm.state_field}`", ""]
        out += _text(sm.doc)
        out += [
            "",
            "```mermaid",
            render_state_machine(sm),
            "```",
            "",
            "| Estat | Nom | Descripció |",
            "|---|---|---|",
        ]
        for s in sm.states:
            flags = " (inicial)" if s.initial else " (final)" if s.final else ""
            out.append(f"| `{s.id}`{flags} | {s.name} | {_cell(s.description) or '—'} |")
        out += ["", "| Transició | Origen | Destí | Descripció |", "|---|---|---|---|"]
        for t in sm.transitions:
            out.append(
                f"| `{t.event}` | `{t.source}` | `{t.target}` | {_cell(t.description) or '—'} |"
            )

    if r.actions:
        out += ["", "## Accions"]
        for a in r.actions:
            out += ["", f"### `{a.name}`", "", f"`{a.qualname}`", ""]
            out += _text(a.doc, heading="####")

    if r.documents:
        out += [
            "",
            "## Documents",
            "",
            "| Ranura | Tipus | Mida màx. | Múltiple | Pujada | Requerit per | Processament "
            "| Descripció |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for slot in r.documents:
            required = ", ".join(f"`{t}`" for t in slot.required_for) or "—"
            out.append(
                f"| `{slot.name}` | {', '.join(slot.content_types)} | {slot.max_bytes} B "
                f"| {'sí' if slot.multiple else 'no'} | {slot.upload} | {required} "
                f"| {'sí' if slot.processing else 'no'} | {_cell(slot.description) or '—'} |"
            )

    if r.hooks:
        out += [
            "",
            "## Hooks del cicle de vida",
            "",
            "| Punt | Hook | Efectes externs | Descripció |",
            "|---|---|---|---|",
        ]
        for h in r.hooks:
            out.append(
                f"| `{h.point}` | `{h.name}` | {'sí' if h.external_effect else 'no'} "
                f"| {_cell(h.doc.summary) or '—'} |"
            )

    out += ["", "## Schemas"]
    for s in r.schemas:
        out += [
            "",
            f"### {s.name} ({s.role})",
            "",
            "| Camp | Tipus | Obligatori | Descripció |",
            "|---|---|---|---|",
        ]
        for f in s.fields:
            out.append(
                f"| `{f.name}` | `{_cell(f.type)}` | {'sí' if f.required else 'no'} "
                f"| {_cell(f.description) or '—'} |"
            )
    return "\n".join(out) + "\n"


def render_markdown(model: DocumentationModel) -> dict[str, str]:
    """Return ``{relative_path: content}``."""
    pages = {f"resources/{r.name}.md": render_resource(r) for r in model.resources}
    index = ["# Catàleg de recursos", "", "| Recurs | Descripció | Operacions |", "|---|---|---|"]
    for r in model.resources:
        ops = ", ".join(f"`{o.name}`" for o in r.operations)
        index.append(
            f"| [{r.name}](resources/{r.name}.md) | {_cell(r.doc.summary) or '—'} | {ops} |"
        )
    if model.system_roles:
        index += ["", "Vegeu també la [matriu de permisos](permissions.md)."]
        pages["permissions.md"] = render_permissions(model)
    pages["index.md"] = "\n".join(index) + "\n"
    return pages


def render_permissions(model: DocumentationModel) -> str:
    """Matrix of system roles × actions. Policies created from the front end are
    data and are listed by the authz_policies resource, not here."""
    roles = model.system_roles
    out = [
        "# Matriu de permisos (rols de sistema)",
        "",
        "Els rols de sistema es defineixen al codi i no es poden modificar des del "
        "front-end. Les polítiques creades des del front-end s'han de consultar a "
        "`authz_policies`.",
        "",
        "| Rol | Descripció | Concessions |",
        "|---|---|---|",
    ]
    for r in roles:
        grants = ", ".join(f"`{g}`" for g in r.grants)
        out.append(f"| `{r.name}` | {_cell(r.description) or '—'} | {grants} |")
    out += ["", "| Acció | " + " | ".join(f"`{r.name}`" for r in roles) + " |"]
    out.append("|---|" + "---|" * len(roles))
    for action in model.actions:
        marks = " | ".join("✓" if action in r.actions else "" for r in roles)
        out.append(f"| `{action}` | {marks} |")
    return "\n".join(out) + "\n"
