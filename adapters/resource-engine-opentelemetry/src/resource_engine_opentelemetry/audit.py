"""Audit entries as OpenTelemetry log records, for log platforms and SIEMs
that receive OTLP (through the Collector: Elastic, Splunk, Datadog, Loki,
Microsoft Sentinel...).

```python
from resource_engine.audit import AuditForwarder
from resource_engine_opentelemetry import OtelAuditExporter

forwarder = AuditForwarder(OtelAuditExporter())   # the global LoggerProvider
```

Each entry is one record: event name ``resource_engine.audit``, severity
INFO (``ok``), WARN (``denied``) or ERROR (``failed``), a one-line body, and
attributes following the OpenTelemetry conventions where one exists
(``enduser.id``, ``enduser.role``, ``event.outcome``); the record's own trace
context links it to the operation's trace when the entry carries one.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from opentelemetry._logs import LogRecord, SeverityNumber, get_logger_provider
from resource_engine.audit.model import AuditEntry

EVENT_NAME = "resource_engine.audit"
_SEVERITY = {
    "ok": (SeverityNumber.INFO, "INFO"),
    "denied": (SeverityNumber.WARN, "WARN"),
    "failed": (SeverityNumber.ERROR, "ERROR"),
}


def audit_attributes(entry: AuditEntry) -> dict[str, Any]:
    """Flat attributes of an entry (OpenTelemetry attribute values are scalars
    or lists of scalars: changes and details travel as JSON)."""
    attributes: dict[str, Any] = {
        "audit.id": entry.id,
        "audit.resource": entry.resource,
        "audit.operation": entry.operation,
        "audit.action": entry.action,
        "event.outcome": {"ok": "success", "denied": "failure", "failed": "failure"}.get(
            entry.outcome, "unknown"
        ),
        "audit.outcome": entry.outcome,
    }
    optional = {
        "enduser.id": entry.actor,
        "enduser.role": ",".join(entry.actor_roles) if entry.actor_roles else None,
        "audit.entity_id": entry.entity_id,
        "audit.tenant": entry.tenant,
        "audit.correlation_id": entry.correlation_id,
        "audit.changes": json.dumps(entry.changes, default=str) if entry.changes else None,
        "audit.details": json.dumps(entry.details, default=str) if entry.details else None,
    }
    attributes.update({k: v for k, v in optional.items() if v is not None})
    return attributes


class OtelAuditExporter:
    """``AuditExporter`` that emits one log record per entry.

    Args:
        logger_provider: Where records go (default: the global provider, set
            up with the SDK and an OTLP exporter by the application).
        name: The instrumentation scope name.
    """

    def __init__(self, logger_provider: Any = None, *, name: str = "resource_engine.audit") -> None:
        provider = logger_provider or get_logger_provider()
        self.logger = provider.get_logger(name)

    async def export(self, entries: Sequence[AuditEntry]) -> None:
        for entry in entries:
            number, text = _SEVERITY.get(entry.outcome, (SeverityNumber.INFO, "INFO"))
            nanos = int(entry.at.timestamp() * 1_000_000_000)
            self.logger.emit(
                LogRecord(
                    timestamp=nanos,
                    observed_timestamp=nanos,
                    severity_number=number,
                    severity_text=text,
                    body=f"{entry.actor or 'anonymous'} {entry.resource}:{entry.action} "
                    f"{entry.outcome}",
                    attributes=audit_attributes(entry),
                    event_name=EVENT_NAME,
                )
            )


__all__ = ["EVENT_NAME", "OtelAuditExporter", "audit_attributes"]
