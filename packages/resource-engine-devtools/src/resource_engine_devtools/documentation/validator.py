"""Documentation coverage rules. Every finding has a stable rule code (RE-D0xx)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

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
    return findings
