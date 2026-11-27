"""Audit entries as OpenTelemetry log records."""

from __future__ import annotations

import json

from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from resource_engine.audit import AuditEntry
from resource_engine_opentelemetry import OtelAuditExporter


async def test_one_log_record_per_entry():
    memory = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(memory))
    exporter = OtelAuditExporter(provider)
    await exporter.export(
        [
            AuditEntry(
                resource="invoices",
                operation="transition",
                action="transition:approve",
                outcome="denied",
                actor="oidc:ada",
                actor_roles=["clerk"],
                entity_id="7",
                tenant="acme",
                changes={"status": {"from": "pending", "to": "approved"}},
            ),
            AuditEntry(resource="invoices", operation="get", action="get", outcome="ok"),
        ]
    )
    denied, read = (r.log_record for r in memory.get_finished_logs())
    assert denied.severity_number == SeverityNumber.WARN and denied.event_name == (
        "resource_engine.audit"
    )
    assert denied.body == "oidc:ada invoices:transition:approve denied"
    attributes = dict(denied.attributes)
    assert attributes["enduser.id"] == "oidc:ada" and attributes["enduser.role"] == "clerk"
    assert attributes["event.outcome"] == "failure" and attributes["audit.tenant"] == "acme"
    assert json.loads(attributes["audit.changes"])["status"]["to"] == "approved"
    assert read.severity_number == SeverityNumber.INFO and "enduser.id" not in read.attributes
