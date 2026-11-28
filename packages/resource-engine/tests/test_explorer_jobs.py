"""The explorer's jobs: a runner reporting to the activity log, "run now"
requests served through the runner (leases hold), and the timed transitions
waiting on one record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from invoices.bootstrap import registry as invoices
from resource_engine import ResourceManager
from resource_engine.application.timers import pending_timers
from resource_engine.jobs import JobRunner
from resource_engine.testing import (
    InMemoryActivityLog,
    InMemoryBlobStore,
    InMemoryLocks,
    InMemoryStore,
)


class Counter:
    def __init__(self, fail: bool = False) -> None:
        self.runs = 0
        self.fail = fail

    async def run_once(self) -> int:
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.runs


async def test_the_runner_registers_its_jobs_and_records_every_run():
    log = InMemoryActivityLog()
    runner = JobRunner(locks=InMemoryLocks(), owner="w1", activity=log)
    runner.add("outbox", Counter(), every=1)
    runner.add("timers", Counter(fail=True), every=timedelta(minutes=1), single=True,
               ttl=timedelta(minutes=2))  # fmt: skip
    await runner.register()
    [worker] = await log.workers()
    jobs = {j.name: j for j in worker.jobs}
    assert worker.owner == "w1" and jobs["outbox"].every == 1.0 and not jobs["outbox"].single
    assert jobs["timers"].single and jobs["timers"].ttl == 120.0 and jobs["timers"].every == 60.0
    await runner.run_once()
    outcomes = {r.job: (r.outcome, r.error) for r in await log.job_runs()}
    assert outcomes["outbox"] == ("ok", None)
    assert outcomes["timers"] == ("failed", "RuntimeError: boom")


async def test_run_now_goes_through_the_runner_and_its_lease():
    log = InMemoryActivityLog()
    locks = InMemoryLocks()
    counter = Counter()
    runner = JobRunner(locks=locks, owner="w1", activity=log)
    runner.add("report", counter, every=3600, single=True)
    first = await log.request_run("report", "oidc:ana")
    unknown = await log.request_run("nope", "oidc:ana")
    assert await runner.serve_requests() == 1
    done = await log.run_request(first.id)
    assert done.status == "ok" and done.claimed_by == "w1" and counter.runs == 1
    assert (await log.run_request(unknown.id)).status == "pending"  # no worker runs it

    # another worker holds the lease: the request is skipped, not run twice
    assert await locks.acquire("report", "w2", timedelta(minutes=5))
    second = await log.request_run("report", "oidc:ana")
    await runner.serve_requests()
    assert (await log.run_request(second.id)).status == "skipped" and counter.runs == 1
    assert [r.outcome for r in await log.job_runs("report")] == ["skipped", "ok"]


async def test_the_runner_serves_requests_while_it_runs():
    import asyncio

    log = InMemoryActivityLog()
    counter = Counter()
    runner = JobRunner(owner="w1", activity=log, requests_every=0.01)
    runner.add("report", counter, every=3600, run_at_start=False)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_forever(stop))
    request = await log.request_run("report", None)
    for _ in range(200):
        if (await log.run_request(request.id)).status == "ok":
            break
        await asyncio.sleep(0.01)
    stop.set()
    await task
    assert counter.runs == 1 and (await log.workers())[0].owner == "w1"


# --------------------------------------------------------------- timers


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def manager() -> ResourceManager:
    return ResourceManager(
        invoices,
        InMemoryStore.from_registry(invoices).unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
    )


async def test_the_timers_waiting_on_a_record(manager):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "1"})
    assert await pending_timers(manager, "invoices", invoice.id) == []  # a draft waits for none
    submitted = await manager.transition("invoices", invoice.id, "submit")
    [timer] = await pending_timers(manager, "invoices", invoice.id)
    assert timer.event == "reject" and timer.after == timedelta(days=30)
    assert timer.due_at == submitted.status_changed_at + timedelta(days=30)
    assert not timer.overdue
    later = datetime.now(UTC) + timedelta(days=31)
    [overdue] = await pending_timers(manager, "invoices", invoice.id, now=later)
    assert overdue.overdue
    assert await pending_timers(manager, "customers", 1) == []  # no timers declared
