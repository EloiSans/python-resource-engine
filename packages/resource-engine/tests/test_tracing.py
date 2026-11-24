"""Spans around operations, outbox publishing, consumers and jobs; the trace
travels with the envelope from the operation to the consumer."""

from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from resource_engine import DomainError, ResourceManager
from resource_engine.jobs import JobRunner
from resource_engine.messaging import (
    EventEnvelope,
    EventRouter,
    MessageConsumer,
    MessageProcessor,
    OutboxDelivery,
    OutboxRelay,
    from_cloudevent,
    to_cloudevent,
)
from resource_engine.testing import InMemoryBlobStore, InMemoryQueue, InMemoryStore, RecordingTracer
from resource_engine.testing.contracts import contract_envelope
from resource_engine.tracing import NoopTracer, get_tracer, set_tracer


@pytest.fixture
def tracer():
    tracer = RecordingTracer()
    previous = set_tracer(tracer)
    yield tracer
    set_tracer(previous)


@pytest.fixture
def manager():
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    manager.store = store
    return manager


async def test_operation_span_and_envelope_trace(tracer, manager):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    span = tracer.named("invoices create")
    assert span.attributes["resource_engine.resource"] == "invoices"
    assert span.attributes["resource_engine.operation"] == "create"
    assert span.attributes["resource_engine.correlation_id"]
    (row,) = manager.store.outbox_records()
    assert row["envelope"].trace == {"traceparent": f"00-{span.trace_id}-{span.span_id}-01"}

    await manager.transition("invoices", invoice.id, "submit")
    transition = tracer.named("invoices transition")
    assert transition.attributes["resource_engine.action"] == "transition:submit"
    assert transition.attributes["resource_engine.entity_id"] == str(invoice.id)
    assert transition.trace_id != span.trace_id  # separate calls, separate traces


async def test_failed_operation_records_the_error(tracer, manager):
    with pytest.raises(DomainError):
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    span = tracer.named("invoices create")
    assert isinstance(span.errors[0], DomainError) and span.ended


async def test_consumer_continues_the_producers_trace(tracer, manager):
    queue = InMemoryQueue()
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await OutboxRelay(manager.store.unit_of_work, queue).run_once()
    publish = tracer.named("outbox publish")
    assert publish.kind == "producer" and publish.attributes["messaging.batch.message_count"] == 1

    router = EventRouter()

    @router.on("ResourceCreated")
    async def submit(envelope):
        await manager.transition("invoices", envelope.payload["entity_id"], "submit")

    processor = MessageProcessor(router, name="billing")
    await MessageConsumer(queue, processor, wait_seconds=0.05).run_once()
    create = tracer.named("invoices create")
    process = tracer.named("billing process")
    assert process.kind == "consumer"
    assert (process.trace_id, process.parent_id) == (create.trace_id, create.span_id)
    assert process.attributes["resource_engine.outcome"] == "ack"
    assert process.attributes["resource_engine.event_type"] == "ResourceCreated"
    nested = tracer.named("invoices transition")
    assert (nested.trace_id, nested.parent_id) == (process.trace_id, process.span_id)
    assert (await manager.get("invoices", invoice.id)).status == "pending"


async def test_failed_message_marks_the_span(tracer):
    async def boom(envelope):
        raise RuntimeError("down")

    queue = InMemoryQueue()
    await queue.publish([contract_envelope(1)])
    await MessageConsumer(queue, MessageProcessor(boom, name="t"), wait_seconds=0.05).run_once()
    span = tracer.named("t process")
    assert span.attributes["resource_engine.outcome"] == "retry"
    assert "RuntimeError: down" in span.errors[0]


async def test_job_span(tracer):
    runner = JobRunner()

    async def tick():
        return 1

    runner.add("tick", tick, every=60)
    await runner.run_once()
    assert tracer.named("job tick").attributes["resource_engine.job"] == "tick"


def test_trace_survives_json_cloudevents_and_attributes():
    trace = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01", "tracestate": "x=1"}
    envelope = contract_envelope(1, trace=trace)
    assert EventEnvelope.from_json(envelope.to_json()) == envelope
    event = to_cloudevent(envelope, source="/test")
    assert event["traceparent"] == trace["traceparent"]
    assert from_cloudevent(event).trace == trace
    assert envelope.attributes()["traceparent"] == trace["traceparent"]
    assert "trace" not in contract_envelope(2).to_dict()  # absent without a tracer


def test_default_is_noop():
    assert isinstance(get_tracer(), NoopTracer)
    assert get_tracer().inject() == {}
