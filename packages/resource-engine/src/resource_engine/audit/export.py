"""Shipping the audit trail to another system (a SIEM, a log platform, an
archive) without losing entries or sending ones that never happened.

```python
sink = CompositeAuditSink(RepositoryAuditSink(uow_factory), OutboxAuditSink(uow_factory))
manager = ResourceManager(..., audit=sink)

forwarder = AuditForwarder(SyslogAuditExporter("siem.internal", 6514, transport="tcp"))
router = ProcessRunner(manager).router().include(forwarder.router())
```

- ``OutboxAuditSink`` stores each entry as an ``AuditRecorded`` message in the
  outbox, in the operation's own transaction: a change that rolls back leaves
  no entry to ship, and a committed one always has one.
- ``AuditForwarder.router()`` hands each message to the exporters; run it
  through ``MessageProcessor(manager=...)`` fed from the outbox (``ProcessFeed``)
  or a broker, so an unreachable SIEM is retried and an entry is not shipped
  twice. When one of several exporters fails, the retry repeats the others:
  they dedupe by the entry's ``id``.
- Exporters (the ``AuditExporter`` port): ``LoggingAuditExporter`` and
  ``SyslogAuditExporter`` (RFC 5424, JSON or CEF, UDP or TCP) here;
  ``OtelAuditExporter`` in ``resource-engine-opentelemetry``;
  ``HttpAuditExporter`` (JSON lines, Splunk HEC, Elastic) in
  ``resource-engine-http``.

See docs/audit.md#shipping-entries.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import socket
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from resource_engine.audit.model import AuditEntry
from resource_engine.audit.recorder import jsonable
from resource_engine.errors import RemoteCallFailed
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import EventEnvelope

AUDIT_EVENT = "AuditRecorded"

log = logging.getLogger("resource_engine.audit")


def entry_to_dict(entry: AuditEntry) -> dict[str, Any]:
    return jsonable(dataclasses.asdict(entry))  # type: ignore[no-any-return]


def entry_from_dict(data: dict[str, Any]) -> AuditEntry:
    known = {f.name for f in dataclasses.fields(AuditEntry)}
    values = {k: v for k, v in data.items() if k in known}
    if isinstance(values.get("at"), str):
        values["at"] = datetime.fromisoformat(values["at"])
    return AuditEntry(**values)


class OutboxAuditSink:
    """``AuditSink`` that queues each entry in the outbox for the exporters.

    Args:
        uow_factory: Opens a unit of work for entries written on their own
            (refused attempts, audited reads); it needs an outbox.
    """

    def __init__(self, uow_factory: Callable[[], Any]) -> None:
        self.uow_factory = uow_factory

    @staticmethod
    def envelopes(entries: Sequence[AuditEntry]) -> list[EventEnvelope]:
        return [
            EventEnvelope(
                type=AUDIT_EVENT,
                payload=entry_to_dict(entry),
                occurred_at=entry.at,
                correlation_id=entry.correlation_id,
                resource=entry.resource,
                key=f"audit:{entry.resource}",
            )
            for entry in entries
        ]

    async def record(self, entries: Sequence[AuditEntry], *, uow: Any = None) -> None:
        if not entries:
            return
        if uow is not None:
            await uow.outbox().add(self.envelopes(entries))
            return
        async with self.uow_factory() as own:
            await own.outbox().add(self.envelopes(entries))
            await own.commit()


class AuditForwarder:
    """Hands ``AuditRecorded`` messages to every exporter, in order."""

    def __init__(self, *exporters: Any) -> None:
        if not exporters:
            raise ValueError("AuditForwarder needs at least one exporter")
        self.exporters = exporters

    async def handle(self, envelope: EventEnvelope) -> None:
        entry = entry_from_dict(dict(envelope.payload))
        for exporter in self.exporters:
            await exporter.export([entry])

    def router(self, *, name: str = "audit-export") -> EventRouter:
        router = EventRouter(
            ignore_unknown=True, name=name, description="Ships audit entries to other systems"
        )
        router.register(AUDIT_EVENT, lambda envelope: self.handle(envelope))
        return router


class LoggingAuditExporter:
    """One JSON log line per entry (for a log shipper that tails the logs)."""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        self.logger = logger or log
        self.level = level

    async def export(self, entries: Sequence[AuditEntry]) -> None:
        for entry in entries:
            self.logger.log(self.level, "audit %s", json.dumps(entry_to_dict(entry)))


# ------------------------------------------------------------------ syslog

#: RFC 5424 facility 13: log audit.
LOG_AUDIT = 13
_SEVERITY = {"ok": 6, "denied": 4, "failed": 3}  # informational, warning, error


def cef(entry: AuditEntry, *, vendor: str, product: str, version: str) -> str:
    """The entry in ArcSight Common Event Format, which most SIEMs parse."""

    def header(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace("|", "\\|")

    def extension(value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        return text.replace("\\", "\\\\").replace("=", "\\=").replace("\n", "\\n").replace("\r", "")

    severity = {"ok": 3, "denied": 6, "failed": 8}.get(entry.outcome, 5)
    fields = {
        "rt": int(entry.at.timestamp() * 1000),
        "suser": entry.actor or "anonymous",
        "act": entry.action,
        "outcome": entry.outcome,
        "cs1Label": "resource",
        "cs1": entry.resource,
        "cs2Label": "entityId",
        "cs2": entry.entity_id or "",
        "cs3Label": "tenant",
        "cs3": entry.tenant or "",
        "cs4Label": "roles",
        "cs4": ",".join(entry.actor_roles),
        "cs5Label": "correlationId",
        "cs5": entry.correlation_id or "",
        "cs6Label": "changes",
        "cs6": entry.changes,
        "externalId": entry.id,
    }
    ext = " ".join(f"{k}={extension(v)}" for k, v in fields.items())
    name = f"{entry.resource} {entry.operation} {entry.outcome}"
    return (
        f"CEF:0|{header(vendor)}|{header(product)}|{header(version)}|"
        f"{header(entry.resource + ':' + entry.action)}|{header(name)}|{severity}|{ext}"
    )


class SyslogAuditExporter:
    """Sends entries as RFC 5424 syslog messages, the format SIEMs collect.

    Args:
        host / port: The collector (514 UDP or TCP; use TLS with a relay such as
            rsyslog or a collector agent on the same host).
        transport: ``udp`` (one datagram per entry) or ``tcp`` (octet counting).
        format: ``cef`` (ArcSight Common Event Format) or ``json``.
        app_name: The syslog APP-NAME and the CEF product.
        hostname: The syslog HOSTNAME (default: this machine's).
        timeout: Seconds for connecting and sending.
    """

    def __init__(
        self,
        host: str,
        port: int = 514,
        *,
        transport: Literal["udp", "tcp"] = "udp",
        format: Literal["cef", "json"] = "cef",
        app_name: str = "resource-engine",
        hostname: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.transport = transport
        self.format = format
        self.app_name = app_name
        self.hostname = hostname or socket.gethostname()
        self.timeout = timeout

    def message(self, entry: AuditEntry) -> bytes:
        from resource_engine import __version__

        priority = LOG_AUDIT * 8 + _SEVERITY.get(entry.outcome, 5)
        timestamp = entry.at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        body = (
            cef(entry, vendor="Resource Engine", product=self.app_name, version=__version__)
            if self.format == "cef"
            else json.dumps(entry_to_dict(entry))
        )
        msgid = "audit"
        return (
            f"<{priority}>1 {timestamp} {self.hostname} {self.app_name} - {msgid} - {body}"
        ).encode()

    async def export(self, entries: Sequence[AuditEntry]) -> None:
        messages = [self.message(e) for e in entries]
        try:
            if self.transport == "udp":
                await asyncio.wait_for(self._udp(messages), self.timeout)
            else:
                await asyncio.wait_for(self._tcp(messages), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise RemoteCallFailed(
                f"syslog {self.host}:{self.port} failed: {exc or type(exc).__name__}",
                service="syslog",
                operation="export",
            ) from exc

    async def _udp(self, messages: list[bytes]) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=(self.host, self.port)
        )
        try:
            for message in messages:
                transport.sendto(message)
        finally:
            transport.close()

    async def _tcp(self, messages: list[bytes]) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            for message in messages:  # RFC 6587 octet counting
                writer.write(f"{len(message)} ".encode() + message)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


__all__ = [
    "AUDIT_EVENT",
    "AuditForwarder",
    "LoggingAuditExporter",
    "OutboxAuditSink",
    "SyslogAuditExporter",
    "cef",
    "entry_from_dict",
    "entry_to_dict",
]
