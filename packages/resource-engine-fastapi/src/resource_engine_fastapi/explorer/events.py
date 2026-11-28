"""The explorer's live events: committed events over server-sent events.

- ``GET <api>/events/stream``: ``text/event-stream``; each event is a frame
  ``event: event``, ``id: <envelope id>``, ``data: {"envelope", "resource",
  "entity_id", "record", "payload_visible"}``. A comment every
  ``keepalive`` seconds keeps proxies from closing a quiet stream.
- ``GET <api>/events/{message_id}/deliveries``: which consumers handled an
  event and how (an ``ActivityLog``).

Visibility is ``LiveEvents``'s: events about records the caller can read; the
payload, and events about no record, only with ``events:read``. Opening a
stream is audited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter
from resource_engine.errors import PermissionDenied
from resource_engine.explorer import EVENTS_READ, allowed, require
from resource_engine.messaging.live import LiveEvent, LiveEvents


def _frame(event: LiveEvent) -> str:
    envelope = event.envelope.to_dict()
    if not event.payload_visible:
        envelope.pop("payload", None)
    record = event.record
    if record is not None:
        from resource_engine.application.relations import dump_read

        record = dump_read(record)
    data = {
        "envelope": envelope,
        "resource": event.resource,
        "entity_id": event.entity_id,
        "record": record,
        "payload_visible": event.payload_visible,
    }
    body = json.dumps(TypeAdapter(Any).dump_python(data, mode="json"), ensure_ascii=False)
    return f"event: event\nid: {event.envelope.id}\ndata: {body}\n\n"


def build_events_router(
    manager: Any,
    hub: Any,
    *,
    actor_dependency: Any,
    activity: Any = None,
    open: bool = False,
    keepalive: float = 15.0,
) -> APIRouter:
    router = APIRouter(prefix="/events", tags=["explorer"])
    live = LiveEvents(manager, hub)

    async def check(actor: Any, resource: str | None, tenant: str | None) -> bool:
        """Whether ``actor`` holds ``events:read`` (everything); otherwise they
        need ``get`` on the resource they filter on, or on some resource."""
        if await allowed(manager, actor, EVENTS_READ.name, open=open):
            return True
        if manager.authorizer is None:
            await require(manager, actor, EVENTS_READ.name, open=open)  # raises
        names = [resource] if resource else [d.name for d in manager.registry if d.exposed("get")]
        for name in names:
            if await manager.can(actor, name, "get", tenant=tenant):
                return False
        await manager.audit_event(actor, EVENTS_READ.name, "denied", details={"resource": resource})
        raise PermissionDenied(
            "Not allowed to see events" + (f" about {resource}" if resource else ""),
            details={"action": f"{resource}:get" if resource else EVENTS_READ.name},
        )

    @router.get(
        "/stream",
        summary="Committed events, live (server-sent events)",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
        operation_id="explorer_event_stream",
    )
    async def stream(
        request: Request,
        resource: str | None = Query(None, description="Only events about this resource"),
        id: str | None = Query(None, description="Only events about this record (with resource)"),
        type: list[str] = Query([], description="Only these event types"),
        version: list[str] = Query([], description="Only these schema versions"),
        tenant: str | None = Query(None, description="Tenant (cross-tenant callers)"),
        actor: Any = Depends(actor_dependency),
    ) -> StreamingResponse:
        if resource is not None:
            manager.registry.get(resource)  # RE-E002 for an unknown resource
        everything = await check(actor, resource, tenant)
        await manager.audit_event(
            actor,
            EVENTS_READ.name if everything else "events:stream",
            details={"resource": resource, "id": id, "types": type, "versions": version},
        )
        events = live.stream(
            actor,
            tenant=tenant,
            resource=resource,
            entity_id=id if resource else None,
            types=type,
            versions=version,
            everything=everything,
        )
        return StreamingResponse(
            _sse(events, request, keepalive),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    if activity is not None:

        @router.get(
            "/{message_id}/deliveries",
            summary="Which consumers handled an event, and how",
            operation_id="explorer_event_deliveries",
        )
        async def deliveries(
            message_id: str, actor: Any = Depends(actor_dependency)
        ) -> list[dict[str, Any]]:
            await check(actor, None, None)
            return [
                {
                    "consumer": d.consumer,
                    "event_type": d.event_type,
                    "outcome": d.outcome,
                    "seconds": d.seconds,
                    "error": d.error,
                    "at": d.at.isoformat(),
                }
                for d in await activity.deliveries(message_id)
            ]

    return router


async def _sse(
    events: AsyncIterator[LiveEvent], request: Request, keepalive: float
) -> AsyncIterator[str]:
    """Frames from ``events`` and a comment every ``keepalive`` seconds, until
    the client goes away."""
    yield ": connected\n\n"
    iterator = events.__aiter__()
    pending: asyncio.Task[LiveEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=keepalive)
            if await request.is_disconnected():
                return
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield _frame(event)
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        closing = getattr(iterator, "aclose", None)
        if closing is not None:
            with contextlib.suppress(BaseException):
                await closing()


__all__ = ["build_events_router"]
