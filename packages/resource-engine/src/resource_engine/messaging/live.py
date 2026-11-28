"""Committed events, live, filtered to what each subscriber may see.

``LiveEvents`` is what the GraphQL subscriptions and the explorer's event
stream share. It reads an ``EventHub`` and applies the visibility rules:

- **Events about a record** (``resource`` and ``entity_id``) reach a
  subscriber only if the record can be read by them now. It is read with
  ``manager.get`` as them, so the permission, the row filter and the tenant
  apply, and the subscriber gets the record as they may see it (hidden fields
  left out).
- **Deletions** reach only subscribers who already got that record in this
  stream: a stream never reveals ids it didn't show.
- **The payload** goes only to subscribers holding ``events:read``, since it
  may carry fields hidden from others. They also see events about no record
  (processes, engine events); everyone else sees the record instead.

```python
live = LiveEvents(manager, hub)
async for event in live.stream(actor, resource="invoices", types={"InvoiceApproved"}):
    event.envelope, event.record, event.payload_visible
```
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from resource_engine.errors import (
    EntityNotFound,
    OperationNotEnabled,
    PermissionDenied,
    ResourceNotRegistered,
    TenantMismatch,
    TenantRequired,
)
from resource_engine.messaging.envelope import EventEnvelope

#: Errors meaning "not visible to this subscriber": the event is skipped.
HIDDEN = (
    EntityNotFound,
    PermissionDenied,
    TenantRequired,
    TenantMismatch,
    OperationNotEnabled,
    ResourceNotRegistered,
)

DELETED = "ResourceDeleted"


@dataclass(frozen=True)
class LiveEvent:
    """One event as a subscriber may see it.

    Attributes:
        envelope: The envelope. Its payload is only for ``payload_visible``.
        resource / entity_id: The record it is about (``None``: no record).
        record: The record as the subscriber reads it now (``None`` for
            deletions and events about no record).
        payload_visible: Whether the subscriber may see ``envelope.payload``.
    """

    envelope: EventEnvelope
    resource: str | None
    entity_id: Any
    record: Any
    payload_visible: bool


class LiveEvents:
    """Committed events from ``hub``, through the visibility rules above."""

    def __init__(self, manager: Any, hub: Any) -> None:
        self.manager = manager
        self.hub = hub

    def about(self, envelope: EventEnvelope) -> tuple[str | None, Any]:
        """The record an event is about: ``(resource, id)``, typed by the
        resource's id type (``(None, None)`` when it is about no record)."""
        payload = envelope.payload or {}
        resource = payload.get("resource") or envelope.resource
        value = payload.get("entity_id")
        if not resource or value is None or resource not in self.manager.registry:
            return None, None
        id_type = self.manager.registry.get(resource).id_type
        try:
            return resource, TypeAdapter(id_type).validate_python(value)
        except Exception:
            return None, None

    async def stream(
        self,
        actor: Any,
        *,
        tenant: str | None = None,
        resource: str | None = None,
        entity_id: Any = None,
        types: Collection[str] = (),
        versions: Collection[str] = (),
        everything: bool = False,
    ) -> AsyncIterator[LiveEvent]:
        """Events ``actor`` may see, as they are committed.

        Args:
            resource / entity_id: Only events about that resource (that record).
            types / versions: Only these event types / schema versions.
            everything: The subscriber holds ``events:read``: payloads, and
                events about no record.
        """
        types, versions = set(types), {str(v) for v in versions}
        if entity_id is not None and resource is not None:
            entity_id = TypeAdapter(self.manager.registry.get(resource).id_type).validate_python(
                entity_id
            )

        def matches(envelope: EventEnvelope) -> bool:
            if types and envelope.type not in types:
                return False
            if versions and str(envelope.schema_version) not in versions:
                return False
            about, about_id = self.about(envelope)
            if resource is not None and about != resource:
                return False
            if entity_id is not None and about_id != entity_id:
                return False
            return about is not None or everything

        subscription = self.hub.subscribe(matches)
        shown: set[tuple[str, Any]] = set()
        try:
            async for envelope in subscription:
                event = await self.visible(envelope, actor, tenant=tenant, shown=shown,
                                           everything=everything)  # fmt: skip
                if event is not None:
                    yield event
        finally:
            subscription.close()

    async def visible(
        self,
        envelope: EventEnvelope,
        actor: Any,
        *,
        tenant: str | None = None,
        shown: set[tuple[str, Any]] | None = None,
        everything: bool = False,
    ) -> LiveEvent | None:
        """``envelope`` as ``actor`` may see it, or ``None``. ``shown`` holds the
        records this subscriber already got (deletions need them)."""
        shown = shown if shown is not None else set()
        resource, entity_id = self.about(envelope)
        if resource is None:
            if not everything:
                return None
            return LiveEvent(envelope, None, None, None, True)
        if envelope.type == DELETED:
            if (resource, entity_id) not in shown:
                return None  # never reveal the id of a record this stream didn't show
            shown.discard((resource, entity_id))
            return LiveEvent(envelope, resource, entity_id, None, everything)
        try:
            record = await self.manager.get(resource, entity_id, actor=actor, tenant=tenant)
        except HIDDEN:
            return None
        shown.add((resource, entity_id))
        return LiveEvent(envelope, resource, entity_id, record, everything)


__all__ = ["HIDDEN", "LiveEvent", "LiveEvents"]
