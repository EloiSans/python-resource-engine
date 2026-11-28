"""Transport-neutral representation of a domain event."""

from __future__ import annotations

import dataclasses
import enum
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from resource_engine.domain.events import DomainEvent
from resource_engine.tracing import get_tracer

#: The payload version of events that declare none (``DomainEvent.schema_version``).
SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class EventEnvelope:
    """A serialised event ready to travel through a broker.

    Attributes:
        id: Unique message id; transports use it for de-duplication.
        type: Event type name (the event class name by default).
        occurred_at: When the event happened (UTC, ISO-8601 on the wire).
        payload: JSON-compatible event fields.
        correlation_id: Id of the operation that produced the event.
        resource: Resource name, when the event comes from a resource operation.
        key: Ordering/partition key (FIFO group, session, ordering key).
        trace: W3C trace context of the operation that produced the event
            (``traceparent``, ``tracestate``), so consumers continue its trace.
        schema_version: Version of the payload's shape for this event type
            (``"1"`` unless the event declares one; older versions published
            side by side carry theirs, see ``resource_engine.versions``).
        tags: Extra string attributes (``origin=console`` on events published
            by hand from the explorer), sent as broker attributes and as
            CloudEvents extensions. Names are lowercase letters and digits.
    """

    type: str
    payload: Mapping[str, Any]
    occurred_at: datetime
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str | None = None
    resource: str | None = None
    key: str | None = None
    trace: Mapping[str, str] | None = None
    schema_version: str = SCHEMA_VERSION
    tags: Mapping[str, str] | None = None

    def attributes(self) -> dict[str, str]:
        """String attributes for broker-level routing and filtering."""
        attrs = {
            "event_type": self.type,
            "event_id": self.id,
            "schema_version": self.schema_version,
        }
        if self.resource:
            attrs["resource"] = self.resource
        if self.correlation_id:
            attrs["correlation_id"] = self.correlation_id
        attrs.update(self.trace or {})
        for name, value in (self.tags or {}).items():
            attrs.setdefault(name, value)
        return attrs

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "resource": self.resource,
            "key": self.key,
            "schema_version": self.schema_version,
            "payload": dict(self.payload),
        }
        if self.trace:
            data["trace"] = dict(self.trace)
        if self.tags:
            data["tags"] = dict(self.tags)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EventEnvelope:
        return cls(
            id=data["id"],
            type=data["type"],
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
            correlation_id=data.get("correlation_id"),
            resource=data.get("resource"),
            key=data.get("key"),
            payload=data.get("payload") or {},
            trace=data.get("trace") or None,
            schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
            tags=dict(data["tags"]) if data.get("tags") else None,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> EventEnvelope:
        return cls.from_dict(json.loads(raw))


def to_jsonable(value: Any) -> Any:
    """Convert common Python values into JSON-compatible ones (lossless as strings)."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(mode="json"))
    raise TypeError(f"Cannot serialise {type(value).__name__} in an event payload")


class EventSerializer:
    """Turns domain events into envelopes.

    The ordering key defaults to ``"<resource>:<entity_id>"`` for resource events,
    so all messages about one entity keep their order on FIFO transports.
    """

    def to_envelopes(
        self,
        event: DomainEvent,
        *,
        correlation_id: str | None = None,
        resource: str | None = None,
    ) -> list[EventEnvelope]:
        """The event's envelope, then one per older version it still
        publishes (``previous_versions``), each with its own id
        (``<id>.v<n>``), ``schema_version`` and converted payload."""
        current = self.to_envelope(event, correlation_id=correlation_id, resource=resource)
        envelopes = [current]
        for number, version in sorted(getattr(event, "previous_versions", {}).items()):
            envelopes.append(
                dataclasses.replace(
                    current,
                    id=f"{current.id}.v{number}",
                    payload=to_jsonable(version.convert(dict(current.payload))),
                    schema_version=str(number),
                )
            )
        return envelopes

    def to_envelope(
        self,
        event: DomainEvent,
        *,
        correlation_id: str | None = None,
        resource: str | None = None,
    ) -> EventEnvelope:
        payload = {
            f.name: to_jsonable(getattr(event, f.name))
            for f in dataclasses.fields(event)
            if f.name != "occurred_at"
        }
        resource = payload.get("resource", resource)
        entity_id = payload.get("entity_id")
        key = f"{resource}:{entity_id}" if resource and entity_id is not None else resource
        return EventEnvelope(
            type=event.name,
            payload=payload,
            occurred_at=event.occurred_at,
            correlation_id=correlation_id,
            resource=resource,
            key=key,
            trace=get_tracer().inject() or None,
            schema_version=str(getattr(event, "schema_version", SCHEMA_VERSION)),
        )
