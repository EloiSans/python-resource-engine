"""CloudEvents 1.0 mapping for ``EventEnvelope`` (structured JSON mode).

SAP Event Mesh, Azure Event Grid and many webhooks speak CloudEvents. The mapping
is lossless: ``from_cloudevent(to_cloudevent(env, source=...)) == env``.

==================  =========================================
CloudEvents         EventEnvelope
==================  =========================================
``id``              ``id``
``source``          given by the publisher (e.g. ``/invoices-service``)
``type``            ``type_prefix + type``
``time``            ``occurred_at``
``subject``         ``key`` (``"<resource>:<entity_id>"``)
``data``            ``payload``
``correlationid``   ``correlation_id`` (extension)
``resource``        ``resource`` (extension)
``partitionkey``    ``key`` (partitioning extension)
other extensions    ``tags`` (``origin=console``...)
==================  =========================================
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from resource_engine.messaging.envelope import SCHEMA_VERSION, EventEnvelope

CONTENT_TYPE = "application/cloudevents+json; charset=utf-8"

#: Attributes this mapping reads; any other extension becomes a tag.
KNOWN = frozenset(
    {
        "specversion", "id", "source", "type", "time", "datacontenttype", "dataschema",
        "schemaversion", "data", "data_base64", "subject", "partitionkey", "correlationid",
        "resource", "traceparent", "tracestate",
    }
)  # fmt: skip


def to_cloudevent(envelope: EventEnvelope, *, source: str, type_prefix: str = "") -> dict[str, Any]:
    """Return the structured-mode CloudEvent as a JSON-compatible dict."""
    event: dict[str, Any] = {
        "specversion": "1.0",
        "id": envelope.id,
        "source": source,
        "type": f"{type_prefix}{envelope.type}",
        "time": envelope.occurred_at.isoformat(),
        "datacontenttype": "application/json",
        "schemaversion": envelope.schema_version,
        "data": dict(envelope.payload),
    }
    if envelope.key:
        event["subject"] = envelope.key
        event["partitionkey"] = envelope.key
    if envelope.correlation_id:
        event["correlationid"] = envelope.correlation_id
    if envelope.resource:
        event["resource"] = envelope.resource
    for name in ("traceparent", "tracestate"):  # distributed tracing extension
        if envelope.trace and envelope.trace.get(name):
            event[name] = envelope.trace[name]
    for name, value in (envelope.tags or {}).items():
        event.setdefault(name, value)
    return event


def to_cloudevent_json(envelope: EventEnvelope, *, source: str, type_prefix: str = "") -> str:
    return json.dumps(
        to_cloudevent(envelope, source=source, type_prefix=type_prefix),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def from_cloudevent(
    event: Mapping[str, Any] | str | bytes, *, type_prefix: str = ""
) -> EventEnvelope:
    """Build an envelope from a structured-mode CloudEvent (dict or JSON)."""
    data = json.loads(event) if isinstance(event, str | bytes) else event
    event_type: str = data["type"]
    if type_prefix and event_type.startswith(type_prefix):
        event_type = event_type[len(type_prefix) :]
    return EventEnvelope(
        id=data["id"],
        type=event_type,
        occurred_at=datetime.fromisoformat(data["time"]),
        payload=data.get("data") or {},
        correlation_id=data.get("correlationid"),
        resource=data.get("resource"),
        key=data.get("partitionkey") or data.get("subject"),
        trace={k: data[k] for k in ("traceparent", "tracestate") if data.get(k)} or None,
        schema_version=str(data.get("schemaversion") or SCHEMA_VERSION),
        tags={k: str(v) for k, v in data.items() if k not in KNOWN} or None,
    )
