"""Background work in one process: periodic jobs and long-running services.

```python
runner = JobRunner(locks=SqlAlchemyLocks(sessions, locks_table(metadata)))
runner.add("outbox", OutboxRelay(uow, publisher), every=1)
runner.add("audit", AuditJanitor(uow, retention=..., sealer=sealer), every=3600, single=True)
runner.add_service("billing", MessageConsumer(source, processor))
await runner.run_forever()
```

A job is anything with ``run_once()`` or an async callable. ``single=True``
jobs take a lease from the ``LockProvider`` before each run, so they run on one
worker at a time however many workers are started. Services are objects with
``run_forever(stop=...)`` (``MessageConsumer``) and run until the runner stops.

With ``activity=`` an ``ActivityLog`` the runner reports to the explorer
(docs/explorer.md#jobs): it registers its jobs and their schedules, records
every run, and runs the jobs someone asked to "run now", through
``run_job`` so leases hold. See docs/jobs.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal, Protocol

from resource_engine.errors import ConfigurationError
from resource_engine.logs import bind_log_context
from resource_engine.ports.locks import LockProvider
from resource_engine.tracing import get_tracer

logger = logging.getLogger("resource_engine.jobs")

type Outcome = Literal["ok", "failed", "skipped"]


class JobObserver(Protocol):
    """Called after every job run: ``(job, outcome, seconds, error)``.
    ``skipped`` means a ``single`` job's lease was held by another worker."""

    def __call__(
        self, job: str, outcome: Outcome, seconds: float, error: BaseException | None
    ) -> Awaitable[None] | None: ...


@dataclass
class Job:
    name: str
    run: Callable[[], Awaitable[Any]]
    every: float
    single: bool = False
    ttl: timedelta = field(default_factory=lambda: timedelta(minutes=10))
    run_at_start: bool = True


@dataclass
class Service:
    name: str
    target: Any


@dataclass(frozen=True)
class JobRun:
    job: str
    outcome: Outcome
    seconds: float
    result: Any = None
    error: BaseException | None = None


def default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _seconds(value: float | timedelta) -> float:
    return value.total_seconds() if isinstance(value, timedelta) else float(value)


class JobRunner:
    """Runs periodic jobs and services until stopped.

    Args:
        locks: Needed for ``single`` jobs.
        owner: This worker's name in leases (host, pid and a random suffix by
            default).
        observers: ``JobObserver`` callbacks, for metrics. Failures are logged
            and ignored.
        activity: An ``ActivityLog``: the jobs, their runs and "run now"
            requests, shared with the explorer.
        requests_every: Seconds between checks for "run now" requests.
        heartbeat: Seconds between re-registrations of the jobs (the
            explorer lists the workers seen recently).
    """

    def __init__(
        self,
        *,
        locks: LockProvider | None = None,
        owner: str | None = None,
        observers: Sequence[JobObserver] = (),
        activity: Any = None,
        requests_every: float = 2.0,
        heartbeat: float = 60.0,
    ) -> None:
        self.locks = locks
        self.owner = owner or default_owner()
        self.observers = list(observers)
        self.activity = activity
        self.requests_every = requests_every
        self.heartbeat = heartbeat
        if activity is not None:
            self.observers.append(ActivityJobObserver(activity, self.owner))
        self.jobs: dict[str, Job] = {}
        self.services: dict[str, Service] = {}

    # ----------------------------------------------------------- registration

    def add(
        self,
        name: str,
        target: Any,
        *,
        every: float | timedelta,
        single: bool = False,
        ttl: timedelta | None = None,
        run_at_start: bool = True,
    ) -> Job:
        """Add a periodic job: ``target.run_once()`` or ``await target()``.

        Args:
            every: Pause between the end of a run and the start of the next.
            single: Run on one worker at a time (needs ``locks``).
            ttl: How long the lease lasts (10 minutes by default). Keep it
                longer than a run: a run that outlasts it may overlap another.
            run_at_start: Run as soon as the runner starts, then every
                ``every``; otherwise wait first.
        """
        self._check_name(name)
        interval = _seconds(every)
        if interval <= 0:
            raise ConfigurationError(f"Job {name!r}: every must be positive")
        if single and self.locks is None:
            raise ConfigurationError(
                f"Job {name!r} is single but the runner has no locks",
                hint="Pass JobRunner(locks=SqlAlchemyLocks(...)) or InMemoryLocks() "
                "for a single worker.",
                doc="docs/jobs.md#one-worker-at-a-time",
            )
        run = getattr(target, "run_once", None) or target
        if not callable(run):
            raise ConfigurationError(f"Job {name!r}: needs run_once() or a callable")
        job = Job(name, run, interval, single, ttl or timedelta(minutes=10), run_at_start)
        if job.ttl <= timedelta(0):
            raise ConfigurationError(f"Job {name!r}: ttl must be positive")
        self.jobs[name] = job
        return job

    def add_service(self, name: str, target: Any) -> Service:
        """Add a long-running service: ``await target.run_forever(stop=event)``."""
        self._check_name(name)
        if not callable(getattr(target, "run_forever", None)):
            raise ConfigurationError(f"Service {name!r}: needs run_forever(stop=...)")
        service = Service(name, target)
        self.services[name] = service
        return service

    def _check_name(self, name: str) -> None:
        if name in self.jobs or name in self.services:
            raise ConfigurationError(f"A job or service named {name!r} already exists")

    # ---------------------------------------------------------------- running

    async def run_job(self, name: str) -> JobRun:
        """Run one job now (honouring ``single``). Errors are caught and reported."""
        job = self.jobs[name]
        if job.single:
            assert self.locks is not None
            if not await self.locks.acquire(job.name, self.owner, job.ttl):
                run = JobRun(job.name, "skipped", 0.0)
                await self._observe(run)
                return run
        started = time.perf_counter()
        try:
            with bind_log_context(job=job.name):
                async with get_tracer().span(
                    f"job {job.name}", attributes={"resource_engine.job": job.name}
                ):
                    result = job.run()
                    if inspect.isawaitable(result):
                        result = await result
            run = JobRun(job.name, "ok", time.perf_counter() - started, result)
        except Exception as exc:
            from resource_engine.reporting import report_error

            with bind_log_context(job=job.name):
                await report_error(exc, where="job", force=True)
            run = JobRun(job.name, "failed", time.perf_counter() - started, error=exc)
        finally:
            if job.single:
                assert self.locks is not None
                try:
                    await self.locks.release(job.name, self.owner)
                except Exception:
                    logger.exception("Job %s: releasing the lease failed", job.name)
        await self._observe(run)
        return run

    def describe(self) -> list[Any]:
        """The jobs and their schedules (``JobInfo``), for the explorer."""
        from resource_engine.ports.activity import JobInfo

        return [
            JobInfo(job.name, job.every, job.single, job.ttl.total_seconds(), job.run_at_start)
            for job in self.jobs.values()
        ]

    async def register(self) -> None:
        """Tell the activity log which jobs this worker runs."""
        if self.activity is not None:
            await self.activity.register_jobs(self.owner, self.describe())

    async def serve_requests(self) -> int:
        """Run the pending "run now" requests for this worker's jobs; returns
        how many. Each goes through ``run_job``, so a ``single`` job whose lease
        another worker holds is ``skipped``."""
        if self.activity is None or not self.jobs:
            return 0
        requests = await self.activity.claim_requests(self.owner, list(self.jobs))
        for request in requests:
            with bind_log_context(job=request.job):
                logger.info("Job %s: run now, asked by %s", request.job, request.requested_by)
            run = await self.run_job(request.job)
            await self.activity.finish_request(request.id, run.outcome, _error_text(run.error))
        return len(requests)

    async def _serve_activity(self, stop: asyncio.Event) -> None:
        last_beat = 0.0
        while not stop.is_set():
            try:
                if time.monotonic() - last_beat >= self.heartbeat:
                    await self.register()
                    last_beat = time.monotonic()
                await self.serve_requests()
            except Exception:
                logger.exception("Worker %s: the activity log failed", self.owner)
            if await _wait(stop, self.requests_every):
                return

    async def run_once(self) -> list[JobRun]:
        """Run every job once, in the order they were added (for cron and tests)."""
        return [await self.run_job(name) for name in self.jobs]

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        """Run jobs on their intervals and every service until ``stop`` is set.
        A service that crashes is logged and restarted after a pause."""
        stop = stop or asyncio.Event()
        if not self.jobs and not self.services:
            raise ConfigurationError("JobRunner has no jobs or services")
        tasks = [
            asyncio.create_task(self._loop(job, stop), name=job.name) for job in self.jobs.values()
        ]
        tasks += [
            asyncio.create_task(self._serve(service, stop), name=service.name)
            for service in self.services.values()
        ]
        if self.activity is not None:
            tasks.append(asyncio.create_task(self._serve_activity(stop), name="activity"))
        logger.info(
            "Worker %s: %d jobs, %d services", self.owner, len(self.jobs), len(self.services)
        )
        try:
            await stop.wait()
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self, job: Job, stop: asyncio.Event) -> None:
        if not job.run_at_start and await _wait(stop, job.every):
            return
        while not stop.is_set():
            await self.run_job(job.name)
            if await _wait(stop, job.every):
                return

    async def _serve(self, service: Service, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await service.target.run_forever(stop=stop)
            except Exception:
                logger.exception("Service %s crashed; restarting in 5 s", service.name)
            else:
                if not stop.is_set():
                    logger.warning("Service %s returned; restarting in 5 s", service.name)
            if await _wait(stop, 5.0):
                return

    async def _observe(self, run: JobRun) -> None:
        for observer in self.observers:
            try:
                result = observer(run.job, run.outcome, run.seconds, run.error)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Job observer failed")


def _error_text(error: BaseException | None) -> str | None:
    if error is None:
        return None
    code = getattr(error, "code", None)
    message = getattr(error, "message", None) or str(error)
    return f"{code or type(error).__name__}: {message}"[:1000]


class ActivityJobObserver:
    """``JobObserver`` recording every run in an ``ActivityLog`` (added by
    ``JobRunner(activity=...)``)."""

    def __init__(self, log: Any, owner: str) -> None:
        self.log = log
        self.owner = owner

    async def __call__(
        self, job: str, outcome: Outcome, seconds: float, error: BaseException | None
    ) -> None:
        from resource_engine.ports.activity import JobRunRecord

        await self.log.record_job_run(
            JobRunRecord(job, outcome, seconds, self.owner, _error_text(error))
        )


async def _wait(stop: asyncio.Event, seconds: float) -> bool:
    """Sleep ``seconds`` or until ``stop``; True when stopped."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    return stop.is_set()


__all__ = [
    "ActivityJobObserver",
    "Job",
    "JobObserver",
    "JobRun",
    "JobRunner",
    "Service",
    "default_owner",
]
