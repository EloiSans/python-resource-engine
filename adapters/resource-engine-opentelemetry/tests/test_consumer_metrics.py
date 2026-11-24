"""ConsumerMetrics against the OpenTelemetry SDK's in-memory reader."""

from __future__ import annotations

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from resource_engine.messaging import MessageConsumer, MessageProcessor
from resource_engine.testing import InMemoryQueue
from resource_engine.testing.contracts import contract_envelope
from resource_engine_opentelemetry import ConsumerMetrics


def points(reader: InMemoryMetricReader, name: str) -> dict[tuple, object]:
    for resource in reader.get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    return {tuple(sorted(p.attributes.items())): p for p in metric.data.data_points}
    return {}


async def handler(envelope):
    if envelope.payload["n"] == 1:
        raise RuntimeError("boom")


async def run(metrics: ConsumerMetrics) -> None:
    queue = InMemoryQueue()
    await queue.publish(
        [contract_envelope(0), contract_envelope(1, key="k"), contract_envelope(2, key="k")]
    )
    processor = MessageProcessor(handler, name="billing", observers=[metrics])
    await MessageConsumer(queue, processor, wait_seconds=0.05).run_once()


def attrs(outcome: str, **extra) -> tuple:
    base = {
        "resource_engine.consumer": "billing",
        "resource_engine.outcome": outcome,
        "resource_engine.event_type": "ContractEvent",
        **extra,
    }
    return tuple(sorted(base.items()))


async def test_counts_outcomes_and_times_handled_messages():
    reader = InMemoryMetricReader()
    await run(ConsumerMetrics(MeterProvider(metric_readers=[reader])))
    counts = points(reader, "resource_engine.consumer.messages")
    assert {k: p.value for k, p in counts.items()} == {
        attrs("ack", **{"resource_engine.blocked": False}): 1,
        attrs("retry", **{"resource_engine.blocked": False}): 1,
        attrs("retry", **{"resource_engine.blocked": True}): 1,
    }
    durations = points(reader, "resource_engine.consumer.duration")
    assert {k: p.count for k, p in durations.items()} == {attrs("ack"): 1, attrs("retry"): 1}


async def test_event_type_can_be_left_out():
    reader = InMemoryMetricReader()
    await run(ConsumerMetrics(MeterProvider(metric_readers=[reader]), include_event_type=False))
    keys = points(reader, "resource_engine.consumer.duration")
    assert all("resource_engine.event_type" not in dict(k) for k in keys)


async def test_without_sdk_it_is_a_no_op():
    await run(ConsumerMetrics())
