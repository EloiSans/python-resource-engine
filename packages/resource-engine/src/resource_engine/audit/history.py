"""A record's state history, read from the audit trail. See docs/audit.md#history.

``manager.history("invoices", 7)`` returns the record's state changes, oldest
first: its creation (with the initial state) and every committed transition,
with who made it and when. The caller needs to be able to ``get`` the record;
the audit trail itself stays restricted.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from resource_engine.application.query import ListQuery
from resource_engine.errors import HistoryNotAvailable

#: Audit operations that change a record's state.
STATE_OPERATIONS = ("create", "transition")
MAX_ENTRIES = 1000


class StateChange(BaseModel):
    """One change of a record's state."""

    at: datetime = Field(description="When it happened (UTC)")
    operation: str = Field(description="create or transition")
    machine: str | None = Field(
        default=None, description="Which state machine (resources with several)"
    )
    event: str | None = Field(default=None, description="Transition event; null for create")
    from_state: Any = Field(default=None, description="State before; null for create")
    to_state: Any = Field(default=None, description="State after")
    actor: str | None = Field(
        default=None, description="Principal id, system:<job> or null (anonymous)"
    )
    correlation_id: str | None = Field(default=None, description="Shared with logs and events")


def history_resource(audit: Any) -> str | None:
    """The audit resource a sink writes to (looking inside composite sinks)."""
    if audit is None:
        return None
    resource = getattr(audit, "resource", None)
    if isinstance(resource, str):
        return resource
    for sink in getattr(audit, "sinks", ()):
        found = history_resource(sink)
        if found is not None:
            return found
    return None


def require_history_resource(manager: Any, definition: Any) -> str:
    if definition.state_field is None or definition.machine is None:
        raise HistoryNotAvailable(
            f"{definition.name} has no state machine, so it has no transition history",
            hint="History covers resources with a state_machine; the audit_log has "
            "every other change.",
            doc="docs/audit.md#history",
        )
    resource = history_resource(manager.audit)
    if resource is None or resource not in {d.name for d in manager.registry}:
        raise HistoryNotAvailable(
            "Transition history is read from the audit trail, which is not stored",
            hint="Call registry.register_audit() and pass audit=True (or a "
            "RepositoryAuditSink) to the ResourceManager.",
            doc="docs/audit.md#history",
        )
    if not definition.resource_cls.audit:
        raise HistoryNotAvailable(
            f"{definition.name} is left out of the audit trail (audit = False)",
            doc="docs/audit.md#history",
        )
    return resource


async def read_history(
    uow: Any,
    audit_resource: str,
    definition: Any,
    entity_id: Any,
    tenant: Any = None,
    machine: str | None = None,
) -> list[StateChange]:
    filters: dict[str, Any] = {
        "resource": definition.name,
        "entity_id": str(entity_id),
        "outcome": "ok",
    }
    if tenant is not None:
        filters["tenant"] = tenant
    repo = uow.repository(audit_resource)
    entries: list[Any] = []
    offset = 0
    while len(entries) < MAX_ENTRIES:
        page = await repo.list(
            ListQuery(offset=offset, limit=500, filters=filters, order_by=["at"])
        )
        entries += page.items
        offset += len(page.items)
        if not page.items or offset >= page.total:
            break
    machines = definition.machines
    if machine is not None and machine not in machines:
        from resource_engine.errors import UnknownTransition

        raise UnknownTransition(
            f"{definition.name} has no state machine {machine!r}",
            hint=f"Machines: {', '.join(machines)}",
        )
    if len(machines) == 1:
        return changes_from(entries, definition.state_field)
    out: list[StateChange] = []
    for name, runner in machines.items():
        if machine is not None and name != machine:
            continue
        events = set(runner.info.events)
        mine = [
            e for e in entries if e.operation == "create" or _event(e.action, e.operation) in events
        ]
        for change in changes_from(mine, runner.state_field):
            change.machine = name
            out.append(change)
    out.sort(key=lambda c: (c.at, c.operation != "create"))
    return out


def changes_from(entries: Sequence[Any], state_field: str) -> list[StateChange]:
    changes: list[StateChange] = []
    for entry in entries:
        if entry.operation not in STATE_OPERATIONS:
            continue
        state = (entry.changes or {}).get(state_field) or {}
        if not isinstance(state, dict) or "changed" in state:  # masked
            state = {}
        changes.append(
            StateChange(
                at=entry.at,
                operation=entry.operation,
                event=_event(entry.action, entry.operation),
                from_state=state.get("from"),
                to_state=state.get("to"),
                actor=entry.actor,
                correlation_id=entry.correlation_id,
            )
        )
    # entries written before creation recorded the initial state
    for created, following in zip(changes, changes[1:], strict=False):
        if created.operation == "create" and created.to_state is None:
            created.to_state = following.from_state
    return changes


def _event(action: str | None, operation: str) -> str | None:
    if operation != "transition" or not action:
        return None
    return action.split(":", 1)[1] if ":" in action else action
