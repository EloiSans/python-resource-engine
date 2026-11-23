"""Builds audit entries from an operation context."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from resource_engine.application.events import ResourceEvent, ResourceUpdated
from resource_engine.audit.model import AuditEntry

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext

MASKED = {"changed": True}


def jsonable(value: Any) -> Any:
    return to_jsonable_python(value, fallback=str)


def actor_id(actor: Any) -> str | None:
    from resource_engine.authz.evaluator import SystemPrincipal

    if isinstance(actor, SystemPrincipal):
        return f"system:{actor.subject}"
    return getattr(actor, "id", None) if actor is not None else None


def action_of(ctx: OperationContext) -> str:
    if ctx.operation == "transition":
        return f"transition:{ctx.transition}"
    if ctx.operation == "action":
        return f"action:{ctx.action}"
    if ctx.operation in ("attach", "detach") and ctx.slot:
        return f"{ctx.operation}:{ctx.slot}"
    return ctx.operation


def masked_fields(ctx: OperationContext) -> frozenset[str]:
    cls = ctx.resource.resource_cls
    return frozenset(ctx.resource.restricted_fields) | frozenset(getattr(cls, "audit_exclude", ()))


def _plain(data: Any) -> dict[str, Any]:
    if isinstance(data, BaseModel):
        return data.model_dump(exclude_unset=True)
    return dict(data) if isinstance(data, dict) else {}


def _mask_values(values: dict[str, Any], masked: frozenset[str]) -> dict[str, Any]:
    return {k: (MASKED if k in masked else jsonable(v)) for k, v in values.items()}


def changes_of(ctx: OperationContext, masked: frozenset[str]) -> dict[str, Any]:
    def entry(name: str, **values: Any) -> Any:
        return MASKED if name in masked else {k: jsonable(v) for k, v in values.items()}

    op = ctx.operation
    if op == "create":
        return {k: entry(k, to=v) for k, v in _plain(ctx.data).items()}
    if op == "update":
        out: dict[str, Any] = {}
        for event in ctx.events:
            if isinstance(event, ResourceUpdated):
                for k, (old, new) in event.changes.items():
                    out[k] = entry(k, **{"from": old, "to": new})
        return out
    if op == "delete" and ctx.entity is not None:
        read = ctx.resource.schemas.get("read")
        names = [n for n in (read.model_fields if read else {}) if n != ctx.resource.id_field]
        return {n: entry(n, **{"from": getattr(ctx.entity, n, None)}) for n in names}
    if op == "transition" and ctx.resource.state_field:
        return {
            ctx.resource.state_field: {
                "from": jsonable(ctx.previous_state),
                "to": jsonable(ctx.new_state),
            }
        }
    return {}


def details_of(ctx: OperationContext, masked: frozenset[str]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if ctx.operation in ("transition", "action") and ctx.data is not None:
        details["input"] = _mask_values(_plain(ctx.data), masked)
    documents = [
        {
            "event": type(e).__name__,
            **{
                k: jsonable(v)
                for k, v in dataclasses.asdict(e).items()
                if k not in ("resource", "entity_id", "occurred_at", "event_id")
            },
        }
        for e in ctx.events
        if isinstance(e, ResourceEvent) and hasattr(e, "document_id")
    ]
    if documents:
        details["documents"] = documents
    return details


def entry_for(
    ctx: OperationContext,
    *,
    roles: list[str],
    outcome: str = "ok",
    action: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEntry:
    masked = masked_fields(ctx)
    ok = outcome == "ok"
    return AuditEntry(
        resource=ctx.resource.name,
        operation=ctx.operation,
        action=action or action_of(ctx),
        outcome=outcome,
        actor=actor_id(ctx.actor),
        actor_roles=roles,
        entity_id=None if ctx.entity_id is None else str(ctx.entity_id),
        changes=changes_of(ctx, masked) if ok else {},
        details={**(details_of(ctx, masked) if ok else {}), **(details or {})},
        correlation_id=ctx.correlation_id,
    )
