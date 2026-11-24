"""JobRunner: periodic jobs, single-worker leases, services and observers."""

from __future__ import annotations

import asyncio

import pytest
from resource_engine.errors import ConfigurationError
from resource_engine.jobs import JobRunner
from resource_engine.testing import InMemoryLocks
from resource_engine.testing.contracts import LockContract


class TestInMemoryLocks(LockContract):
    @pytest.fixture
    def lock_provider(self):
        return InMemoryLocks()


class Counter:
    def __init__(self, fail: bool = False) -> None:
        self.runs = 0
        self.fail = fail

    async def run_once(self) -> int:
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.runs


async def test_run_once_runs_every_job_and_reports():
    seen = []
    runner = JobRunner(observers=[lambda *a: seen.append(a[:2])])
    ok, bad = Counter(), Counter(fail=True)
    calls = []

    async def plain():
        calls.append(1)

    runner.add("ok", ok, every=60)
    runner.add("bad", bad, every=60)
    runner.add("plain", plain, every=60)
    runs = await runner.run_once()
    assert [(r.job, r.outcome) for r in runs] == [("ok", "ok"), ("bad", "failed"), ("plain", "ok")]
    assert runs[0].result == 1 and isinstance(runs[1].error, RuntimeError) and calls == [1]
    assert seen == [("ok", "ok"), ("bad", "failed"), ("plain", "ok")]


async def test_single_jobs_skip_while_another_worker_holds_the_lease():
    locks = InMemoryLocks()
    one, two = JobRunner(locks=locks, owner="one"), JobRunner(locks=locks, owner="two")
    job = Counter()
    one.add("seal", job, every=60, single=True)
    two.add("seal", job, every=60, single=True)
    from datetime import timedelta

    await locks.acquire("seal", "one", timedelta(minutes=1))  # one is running it
    assert (await two.run_job("seal")).outcome == "skipped"
    await locks.release("seal", "one")
    assert (await two.run_job("seal")).outcome == "ok"
    assert locks.holder("seal") is None  # released after the run
    assert job.runs == 1


async def test_single_jobs_overlapping_in_time_run_once():
    locks = InMemoryLocks()
    started, release = asyncio.Event(), asyncio.Event()
    runs = []

    async def slow():
        runs.append(1)
        started.set()
        await release.wait()

    a, b = JobRunner(locks=locks), JobRunner(locks=locks)
    a.add("j", slow, every=60, single=True)
    b.add("j", slow, every=60, single=True)
    first = asyncio.create_task(a.run_job("j"))
    await started.wait()
    assert (await b.run_job("j")).outcome == "skipped"
    release.set()
    assert (await first).outcome == "ok" and runs == [1]


async def test_run_forever_repeats_jobs_and_runs_services_until_stopped():
    runner = JobRunner()
    job = Counter()
    runner.add("tick", job, every=0.01)

    class Service:
        def __init__(self):
            self.stopped = False

        async def run_forever(self, stop):
            await stop.wait()
            self.stopped = True

    service = Service()
    runner.add_service("consumer", service)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_forever(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert job.runs >= 3 and service.stopped


async def test_crashing_service_restarts(monkeypatch):
    import resource_engine.jobs as jobs

    async def quick(stop, seconds):
        await asyncio.sleep(0)
        return stop.is_set()

    monkeypatch.setattr(jobs, "_wait", quick)
    attempts = []

    class Flaky:
        async def run_forever(self, stop):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("down")
            await stop.wait()

    runner = JobRunner()
    runner.add_service("flaky", Flaky())
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_forever(stop))
    for _ in range(50):
        if len(attempts) >= 3:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, 1)
    assert len(attempts) == 3


async def test_failing_observer_is_ignored():
    def broken(*args):
        raise ValueError("observer")

    runner = JobRunner(observers=[broken])
    runner.add("ok", Counter(), every=1)
    assert [r.outcome for r in await runner.run_once()] == ["ok"]


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda r: r.add("x", Counter(), every=0), "every"),
        (lambda r: r.add("x", Counter(), every=1, single=True), "no locks"),
        (lambda r: r.add("x", object(), every=1), "run_once"),
        (lambda r: r.add_service("x", Counter()), "run_forever"),
        (lambda r: (r.add("x", Counter(), every=1), r.add("x", Counter(), every=1)), "exists"),
    ],
)
def test_configuration_errors(call, match):
    with pytest.raises(ConfigurationError, match=match):
        call(JobRunner())


async def test_runner_without_work_refuses_to_start():
    with pytest.raises(ConfigurationError):
        await JobRunner().run_forever()
