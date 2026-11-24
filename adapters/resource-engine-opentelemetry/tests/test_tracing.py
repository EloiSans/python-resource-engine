"""OtelTracer with the SDK's in-memory exporter: operation, outbox and consumer
spans form one trace."""

from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from resource_engine import DomainError, ResourceManager
from resource_engine.messaging import (
    EventRouter,
    MessageConsumer,
    MessageProcessor,
    OutboxDelivery,
    OutboxRelay,
)
from resource_engine.testing import InMemoryBlobStore, InMemoryQueue, InMemoryStore
from resource_engine.tracing import set_tracer
from resource_engine_opentelemetry import OtelTracer


@pytest.fixture
def exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = set_tracer(OtelTracer(provider))
    yield exporter
    set_tracer(previous)


def spans(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_one_trace_from_operation_to_consumer(exporter):
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        event_delivery=OutboxDelivery(),
        blob_store=InMemoryBlobStore(),
    )
    queue = InMemoryQueue()
    await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await OutboxRelay(store.unit_of_work, queue).run_once()
    router = EventRouter()

    @router.on("ResourceCreated")
    async def submit(envelope):
        await manager.transition("invoices", envelope.payload["entity_id"], "submit")

    await MessageConsumer(
        queue, MessageProcessor(router, name="billing"), wait_seconds=0.05
    ).run_once()
    found = spans(exporter)
    create, process, transition = (
        found["invoices create"],
        found["billing process"],
        found["invoices transition"],
    )
    assert process.kind is SpanKind.CONSUMER and found["outbox publish"].kind is SpanKind.PRODUCER
    assert process.context.trace_id == create.context.trace_id
    assert process.parent.span_id == create.context.span_id
    assert transition.parent.span_id == process.context.span_id
    assert transition.attributes["resource_engine.action"] == "transition:submit"
    assert process.attributes["resource_engine.outcome"] == "ack"


async def test_errors_set_status_and_type(exporter):
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, blob_store=InMemoryBlobStore())
    with pytest.raises(DomainError) as raised:
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    span = spans(exporter)["invoices create"]
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == raised.value.code
    assert span.events[0].name == "exception"
