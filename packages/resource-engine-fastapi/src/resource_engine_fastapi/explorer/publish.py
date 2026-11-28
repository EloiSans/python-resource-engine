"""The explorer's PUB rows: publishing an event by hand.

- ``POST <api>/publish/dry-run``: ``{type, version, payload}`` → the problems
  of the payload against the event and its consumers; nothing is written.
- ``POST <api>/publish``: the same, published through the outbox, tagged
  ``origin=console`` and audited (202 with the envelopes).

Both need ``events:publish:<Event>``, granted only by name; publishing also
needs ``EventPublishing(allow_publish=True)``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    type: str = Field(description="Event type (InvoiceApproved)")
    version: int | None = Field(default=None, description="Schema version (default: current)")
    payload: dict[str, Any] = Field(default_factory=dict, description="The event's fields")


def build_publish_router(publishing: Any, *, actor_dependency: Any) -> APIRouter:
    router = APIRouter(prefix="/publish", tags=["explorer"])

    @router.post("/dry-run", summary="Check an event's payload without publishing it")
    async def dry_run(
        request: PublishRequest, actor: Any = Depends(actor_dependency)
    ) -> dict[str, Any]:
        problems = await publishing.validate(
            request.type, request.version, request.payload, actor=actor
        )
        return {
            "problems": [dataclasses.asdict(p) for p in problems],
            "blocking": any(p.check == "event" for p in problems),
        }

    @router.post(
        "", summary="Publish an event through the outbox", status_code=status.HTTP_202_ACCEPTED
    )
    async def publish(
        request: PublishRequest, actor: Any = Depends(actor_dependency)
    ) -> JSONResponse:
        envelopes = await publishing.publish(
            request.type, request.version, request.payload, actor=actor
        )
        return JSONResponse(
            {"envelopes": [e.to_dict() for e in envelopes]},
            status_code=status.HTTP_202_ACCEPTED,
        )

    return router


def publish_config(publishing: Any, api_prefix: str) -> dict[str, Any]:
    """What the PUB rows need: the publishable events by resource."""
    by_resource: dict[str, list[dict[str, Any]]] = {}
    for event in publishing.catalogue():
        by_resource.setdefault(event.resource or "", []).append(
            {
                "type": event.type,
                "summary": event.summary,
                "versions": [
                    {"version": v.version, "example": v.example, "deprecated": v.deprecated}
                    for v in event.versions
                ],
            }
        )
    return {
        "dry_run": f"{api_prefix}/publish/dry-run",
        "publish": f"{api_prefix}/publish",
        "enabled": publishing.allow_publish,
        "events": by_resource,
    }


__all__ = ["PublishRequest", "build_publish_router", "publish_config"]
