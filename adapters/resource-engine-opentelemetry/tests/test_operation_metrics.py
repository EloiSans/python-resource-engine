"""OperationMetrics and JobMetrics against the SDK's in-memory reader."""

from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from resource_engine import DomainError, ResourceManager
from resource_engine.jobs import JobRunner
from resource_engine.testing import InMemoryBlobStore, InMemoryLocks, InMemoryStore
from resource_engine_opentelemetry import JobMetrics, OperationMetrics


def points(reader: InMemoryMetricReader, name: str) -> dict[frozenset, object]:
    data = reader.get_metrics_data()
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    return {frozenset(p.attributes.items()): p for p in metric.data.data_points}
    return {}


def manager_with(metrics: OperationMetrics) -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry, store.unit_of_work, blob_store=InMemoryBlobStore(), observers=[metrics]
    )


async def test_operation_durations_by_outcome():
    reader = InMemoryMetricReader()
    manager = manager_with(OperationMetrics(MeterProvider(metric_readers=[reader])))
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")
    with pytest.raises(DomainError) as raised:
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    found = {k: p.count for k, p in points(reader, "resource_engine.operation.duration").items()}
    base = {"resource_engine.resource": "invoices"}
    assert found == {
        frozenset({**base, "resource_engine.operation": "create",
                   "resource_engine.outcome": "ok"}.items()): 1,
        frozenset({**base, "resource_engine.operation": "transition",
                   "resource_engine.outcome": "ok",
                   "resource_engine.action": "transition:submit"}.items()): 1,
        frozenset({**base, "resource_engine.operation": "create",
                   "resource_engine.outcome": "failed",
                   "error.type": raised.value.code}.items()): 1,
    }  # fmt: skip


async def test_action_can_be_left_out():
    reader = InMemoryMetricReader()
    metrics = OperationMetrics(MeterProvider(metric_readers=[reader]), include_action=False)
    manager = manager_with(metrics)
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")
    keys = points(reader, "resource_engine.operation.duration")
    assert all("resource_engine.action" not in dict(k) for k in keys)


async def test_job_runs_and_durations():
    reader = InMemoryMetricReader()
    runner = JobRunner(
        locks=InMemoryLocks(), observers=[JobMetrics(MeterProvider(metric_readers=[reader]))]
    )

    async def fails():
        raise RuntimeError("boom")

    async def ok():
        return 1

    runner.add("ok", ok, every=60)
    runner.add("fails", fails, every=60)
    runner.add("single", ok, every=60, single=True)
    await runner.locks.acquire("single", "someone-else", runner.jobs["single"].ttl)
    await runner.run_once()
    runs = {k: p.value for k, p in points(reader, "resource_engine.job.runs").items()}
    assert runs == {
        frozenset({"resource_engine.job": "ok", "resource_engine.outcome": "ok"}.items()): 1,
        frozenset({"resource_engine.job": "fails", "resource_engine.outcome": "failed",
                   "error.type": "RuntimeError"}.items()): 1,
        frozenset({"resource_engine.job": "single",
                   "resource_engine.outcome": "skipped"}.items()): 1,
    }  # fmt: skip
    durations = points(reader, "resource_engine.job.duration")
    assert {dict(k)["resource_engine.job"] for k in durations} == {"ok", "fails"}


async def test_without_sdk_it_is_a_no_op():
    manager = manager_with(OperationMetrics())
    await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    JobMetrics()("job", "ok", 0.1, None)
