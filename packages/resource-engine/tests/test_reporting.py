"""Error reporting: one report per unexpected failure, wherever it happens."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from invoices.bootstrap import registry
from resource_engine import ListQuery, ResourceManager, reporting
from resource_engine.errors import RemoteCallFailed, ValidationFailed
from resource_engine.jobs import JobRunner
from resource_engine.messaging import EventEnvelope, EventRouter, MessageProcessor
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


class Collect:
    def __init__(self) -> None:
        self.reports: list[reporting.ErrorReport] = []

    async def report(self, report: reporting.ErrorReport) -> None:
        self.reports.append(report)


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def collected():
    sink = Collect()
    reporting.set_reporter(sink, logging_too=False)
    yield sink.reports
    reporting.set_reporter()


def _manager(**kw) -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        **kw,
    )


async def test_once_per_failure_and_expected_errors_skipped(collected):
    error = RemoteCallFailed("down", service="billing", operation="get")
    await reporting.report_error(error, where="operation", service="billing")
    await reporting.report_error(error, where="http")
    await reporting.report_error(ValidationFailed("bad"), where="operation")
    await reporting.report_error(ValidationFailed("bad"), where="consumer", force=True)
    assert [(r.where, r.code, r.level) for r in collected] == [
        ("operation", "RE-E501", "error"),
        ("consumer", "RE-E101", "warning"),
    ]
    assert collected[0].context["service"] == "billing"
    assert reporting.is_expected(ValidationFailed("x")) and not reporting.is_expected(error)
    assert not reporting.is_expected(RuntimeError("x"))


async def test_a_broken_reporter_does_not_hide_the_failure(caplog):
    class Broken:
        def report(self, report):
            raise RuntimeError("reporter down")

    reporting.set_reporter(Broken())
    try:
        await reporting.report_error(RuntimeError("boom"), where="job")
    finally:
        reporting.set_reporter()
    assert "boom" in caplog.text and "reporter down" in caplog.text


async def test_operations_report_unexpected_failures(collected, monkeypatch):
    manager = _manager()
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})

    def explode(ctx):
        raise RuntimeError("disk full")

    definition = registry.get("invoices")
    monkeypatch.setattr(
        definition.dispatcher,
        "for_point",
        lambda point, qualifier=None, _f=definition.dispatcher.for_point: (
            [explode] if point.value == "before_update" else _f(point, qualifier)
        ),
    )
    with pytest.raises(RuntimeError):
        await manager.update("invoices", invoice.id, {"customer": "Other"})
    with pytest.raises(ValidationFailed):
        await manager.create("invoices", {"amount": "1"})  # expected: not reported
    [report] = collected
    assert (report.where, report.code) == ("operation", "RuntimeError")
    assert report.context["resource"] == "invoices"
    assert report.context["operation"] == "update"
    assert report.context["correlation_id"]


async def test_dead_letters_and_jobs_are_reported(collected):
    router = EventRouter()

    async def handler(envelope):
        raise ValidationFailed("unusable")  # permanent: dead-lettered at once

    router.register("Broken", handler)
    processor = MessageProcessor(router, name="orders")
    envelope = EventEnvelope(type="Broken", payload={}, occurred_at=datetime.now(UTC))
    await processor.process(ReceivedMessage(envelope, message_id=envelope.id))

    runner = JobRunner()

    async def job():
        raise RuntimeError("cron broke")

    runner.add("nightly", job, every=60)
    await runner.run_once()
    assert [(r.where, r.level, r.code) for r in collected] == [
        ("consumer", "warning", "RE-E101"),
        ("job", "error", "RuntimeError"),
    ]
    assert collected[0].context["consumer"] == "orders"
    assert collected[0].context["outcome"] == "dead_letter"
    assert collected[1].context["job"] == "nightly"


async def test_failed_operations_in_the_audit_trail(monkeypatch):
    manager = _manager(audit=True, audit_failures=True)
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})
    await manager.transition("invoices", invoice.id, "submit")
    with pytest.raises(Exception, match="no longer be modified"):  # a business rule
        await manager.update("invoices", invoice.id, {"customer": "Other"})
    entries = await manager.list(
        "audit_log", ListQuery(filters={"resource": "invoices", "outcome": "failed"})
    )
    [failed] = entries.items
    assert failed.operation == "update"
    assert failed.details["code"] == "INVOICE"
    assert failed.entity_id == str(invoice.id)
    assert failed.changes == {}

    quiet = _manager(audit=True)  # off by default
    other = await quiet.create("invoices", {"customer": "ACME", "amount": "1"})
    await quiet.transition("invoices", other.id, "submit")
    with pytest.raises(Exception, match="no longer be modified"):
        await quiet.update("invoices", other.id, {"customer": "Other"})
    none = await quiet.list("audit_log", ListQuery(filters={"outcome": "failed"}))
    assert none.total == 0
