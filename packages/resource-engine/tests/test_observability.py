"""Operation observers, the log context and JSON logs."""

from __future__ import annotations

import io
import json
import logging

import pytest
from invoices.bootstrap import registry
from resource_engine import DomainError, ResourceEngineError, ResourceManager
from resource_engine.jobs import JobRunner
from resource_engine.logs import (
    JsonFormatter,
    LogContextFilter,
    bind_log_context,
    configure_logging,
    log_context,
)
from resource_engine.messaging import MessageConsumer, MessageProcessor
from resource_engine.ports.observability import OperationRun
from resource_engine.testing import InMemoryBlobStore, InMemoryQueue, InMemoryStore, RecordingTracer
from resource_engine.testing.contracts import contract_envelope
from resource_engine.tracing import set_tracer


class Notifier:
    def __init__(self) -> None:
        self.contexts: list[dict] = []

    async def send(self, message: str) -> None:
        self.contexts.append(log_context())


def make_manager(*observers, notifier=None) -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": notifier} if notifier else None,
        observers=observers,
    )


# ---------------------------------------------------------------- observers


async def test_observers_see_every_operation():
    runs: list[OperationRun] = []

    async def async_observer(run: OperationRun) -> None:
        runs.append(run)

    def broken(run: OperationRun) -> None:
        raise RuntimeError("observer down")

    manager = make_manager(broken, async_observer)
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")
    await manager.get("invoices", invoice.id)
    with pytest.raises(DomainError) as raised:
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})

    assert [(r.operation, r.outcome) for r in runs] == [
        ("create", "ok"),
        ("transition", "ok"),
        ("get", "ok"),
        ("create", "failed"),
    ]
    assert all(r.resource == "invoices" and r.seconds >= 0 for r in runs)
    assert runs[1].action == "transition:submit" and runs[0].action is None
    assert all(r.correlation_id for r in runs)
    assert len({r.correlation_id for r in runs}) == 4
    failed = runs[-1]
    assert failed.error is raised.value and failed.error_type == raised.value.code


async def test_operation_failing_before_its_context():
    runs: list[OperationRun] = []
    manager = make_manager(runs.append)
    with pytest.raises(ResourceEngineError) as raised:
        await manager.get("no_such_resource", 1)
    [run] = runs
    assert run.resource == "no_such_resource" and run.outcome == "failed"
    assert run.error_type == raised.value.code and run.correlation_id is None


# -------------------------------------------------------------- log context


async def test_log_context_inside_an_operation():
    notifier = Notifier()
    runs: list[OperationRun] = []
    manager = make_manager(runs.append, notifier=notifier)
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")
    await manager.documents.attach(
        "invoices", invoice.id, "signed_pdf", b"%PDF-1.7",
        filename="signada.pdf", content_type="application/pdf",
    )  # fmt: skip
    await manager.transition("invoices", invoice.id, "approve")
    [context] = notifier.contexts  # the after_commit hook notifies on approve
    assert context == {
        "resource": "invoices",
        "operation": "transition",
        "correlation_id": runs[-1].correlation_id,
        "entity_id": str(invoice.id),
        "action": "transition:approve",
    }
    assert log_context() == {}


async def test_consumer_and_nested_operation_contexts():
    manager = make_manager()
    seen: dict[str, dict] = {}

    async def handler(envelope):
        seen["handler"] = log_context()
        await manager.create("invoices", {"customer": "ACME", "amount": "10"})
        seen["after"] = log_context()

    class Observer:
        def __call__(self, run):
            seen["operation"] = log_context()

    manager.observers.append(Observer())
    queue = InMemoryQueue()
    envelope = contract_envelope(0)
    envelope = type(envelope)(**{**envelope.__dict__, "correlation_id": "corr-1"})
    await queue.publish([envelope])
    await MessageConsumer(
        queue, MessageProcessor(handler, name="billing"), wait_seconds=0.05
    ).run_once()

    assert seen["handler"] == {
        "consumer": "billing",
        "message_id": envelope.id,
        "event_type": "ContractEvent",
        "correlation_id": "corr-1",
    }
    inner = seen["operation"]
    assert inner["consumer"] == "billing" and inner["operation"] == "create"
    assert inner["correlation_id"] != "corr-1"  # the operation's own
    assert seen["after"] == seen["handler"]


async def test_job_context():
    seen = []

    async def job():
        seen.append(log_context())

    runner = JobRunner()
    runner.add("cleanup", job, every=60)
    await runner.run_once()
    assert seen == [{"job": "cleanup"}]


def test_bind_log_context_nests_and_skips_none():
    with bind_log_context(job="a", resource=None):
        with bind_log_context(consumer="b"):
            assert log_context() == {"job": "a", "consumer": "b"}
        assert log_context() == {"job": "a"}
    assert log_context() == {}


# -------------------------------------------------------------------- JSON


def _record(logger: logging.Logger, formatter: logging.Formatter, emit) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        emit()
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_json_lines_carry_context_extras_and_errors():
    logger = logging.getLogger("test.json")
    tracer = RecordingTracer()

    def emit():
        with bind_log_context(resource="invoices", operation="create", correlation_id="c1"):
            logger.info("created %s", 7, extra={"amount": 10})
            try:
                raise DomainError("bad", code="INVOICE_BAD")
            except DomainError:
                logger.exception("failed")

    previous = set_tracer(tracer)
    try:
        output = _record(logger, JsonFormatter(static={"service": "billing"}), emit)
    finally:
        set_tracer(previous)
    first, second = (json.loads(line) for line in output.splitlines())
    assert first["message"] == "created 7" and first["level"] == "INFO"
    assert first["logger"] == "test.json" and first["service"] == "billing"
    assert first["timestamp"].endswith("+00:00")
    assert first["resource"] == "invoices" and first["correlation_id"] == "c1"
    assert first["amount"] == 10
    assert "trace_id" not in first and "exception" not in first
    assert second["error_type"] == "INVOICE_BAD"
    assert "Traceback" in second["exception"]


async def test_json_lines_carry_trace_ids():
    logger = logging.getLogger("test.json.trace")
    tracer = RecordingTracer()
    previous = set_tracer(tracer)
    lines: list[str] = []
    try:
        async with tracer.span("work"):
            lines.append(_record(logger, JsonFormatter(), lambda: logger.info("inside")))
    finally:
        set_tracer(previous)
    line = json.loads(lines[0])
    span = tracer.named("work")
    assert line["trace_id"] == span.trace_id and line["span_id"] == span.span_id


def test_text_format_shows_correlation_id():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    stream = io.StringIO()
    try:
        handler = configure_logging(format="text", level="debug", stream=stream)
        assert any(isinstance(f, LogContextFilter) for f in handler.filters)
        with bind_log_context(correlation_id="abc"):
            logging.getLogger("test.text").info("hello")
        logging.getLogger("test.text").info("outside")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    first, second = stream.getvalue().splitlines()
    assert first.endswith("[abc] hello") and second.endswith("[None] outside")


def test_configure_logging_json_and_bad_format():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    stream = io.StringIO()
    try:
        configure_logging(format="json", stream=stream)
        logging.getLogger("test.cfg").warning("hi")
        with pytest.raises(ValueError):
            configure_logging(format="xml")  # type: ignore[arg-type]
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    assert json.loads(stream.getvalue())["message"] == "hi"


def test_update_outside_a_block_is_a_no_op():
    from resource_engine.logs import update_log_context

    update_log_context(job="leak")
    assert log_context() == {}
