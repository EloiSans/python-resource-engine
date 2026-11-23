from __future__ import annotations

from resource_engine_devtools.documentation.model import StateMachineDoc


def render_state_machine(sm: StateMachineDoc) -> str:
    """Mermaid ``stateDiagram-v2`` built from the executable machine definition."""
    lines = ["stateDiagram-v2"]
    for s in sm.states:
        label = s.name if s.name and s.name != s.id else s.id
        lines.append(f"    {s.id}: {label}")
    for s in sm.states:
        if s.initial:
            lines.append(f"    [*] --> {s.id}")
    for t in sm.transitions:
        lines.append(f"    {t.source} --> {t.target}: {t.event}")
    for s in sm.states:
        if s.final:
            lines.append(f"    {s.id} --> [*]")
    return "\n".join(lines)
