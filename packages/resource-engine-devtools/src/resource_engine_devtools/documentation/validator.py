"""Documentation coverage rules. Every finding has a stable rule code (RE-D0xx)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from resource_engine_devtools.documentation.model import DocumentationModel


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning" | "ok"
    code: str
    target: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def validate(model: DocumentationModel) -> list[Finding]:
    findings: list[Finding] = []

    def check(ok: bool, level: str, code: str, target: str, message: str) -> None:
        findings.append(Finding("ok" if ok else level, code, target, message))

    for r in model.resources:
        check(r.doc.present, "error", "RE-D001", r.qualname, "resource has a description")
        for op in r.operations:
            if op.kind == "crud":
                check(
                    op.doc.present,
                    "error",
                    "RE-D002",
                    f"{r.name}.{op.name}",
                    "CRUD operation is documented (operation_docs)",
                )
        if r.state_machine:
            sm = r.state_machine
            check(
                sm.doc.present, "error", "RE-D003", sm.qualname, "state machine has a description"
            )
            for s in sm.states:
                check(
                    bool(s.description),
                    "warning",
                    "RE-D004",
                    f"{sm.name}.{s.id}",
                    "state is documented (States: section)",
                )
            for event in dict.fromkeys(t.event for t in sm.transitions):
                documented = any(t.description for t in sm.transitions if t.event == event)
                check(
                    documented,
                    "warning",
                    "RE-D005",
                    f"{sm.name}.{event}",
                    "transition is documented (Transitions: section)",
                )
        for a in r.actions:
            check(a.doc.present, "error", "RE-D006", a.qualname, "action has a description")
            if a.doc.present:
                check(
                    "postconditions" in a.doc.sections,
                    "warning",
                    "RE-D007",
                    a.qualname,
                    "action documents its Postconditions",
                )
        for slot in r.documents:
            check(
                bool(slot.description),
                "warning",
                "RE-D009",
                f"{r.qualname}.documents.{slot.name}",
                "document slot has a description",
            )
        for h in r.hooks:
            check(
                h.doc.present, "warning", "RE-D008", h.qualname, f"hook at {h.point} is documented"
            )
        for target, text in _texts(r):
            if PLACEHOLDER.search(text):
                findings.append(
                    Finding("warning", "RE-D010", target, "documentation is a TODO placeholder")
                )
    return findings


#: Placeholder text left by ``resource-engine scaffold``.
PLACEHOLDER = re.compile(r"\bTODO\b")


def _doc_texts(target: str, doc: Any) -> list[tuple[str, str]]:
    texts = [doc.summary or "", doc.description or "", *doc.sections.values()]
    texts += [d for items in doc.items.values() for d in items.values()]
    return [(target, t) for t in texts if t]


def _texts(r: Any) -> list[tuple[str, str]]:
    """Every documentation text of a resource, with the element it describes."""
    found = _doc_texts(r.qualname, r.doc)
    for op in r.operations:
        found += _doc_texts(f"{r.name}.{op.name}", op.doc)
    if r.state_machine:
        sm = r.state_machine
        found += _doc_texts(sm.qualname, sm.doc)
        found += [(f"{sm.name}.{s.id}", s.description or "") for s in sm.states]
        found += [(f"{sm.name}.{t.event}", t.description or "") for t in sm.transitions]
    for a in r.actions:
        found += _doc_texts(a.qualname, a.doc)
    for h in r.hooks:
        found += _doc_texts(h.qualname, h.doc)
    for slot in r.documents:
        found.append((f"{r.qualname}.documents.{slot.name}", slot.description or ""))
    for schema in getattr(r, "schemas", []) or []:
        doc = getattr(schema, "doc", None)
        if doc is not None:
            found += _doc_texts(getattr(schema, "qualname", r.qualname), doc)
    seen: set[tuple[str, str]] = set()
    return [x for x in found if x[1] and not (x in seen or seen.add(x))]
