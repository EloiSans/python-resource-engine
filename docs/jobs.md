# Background jobs

The engine's background work runs in a worker process. Some of it is periodic:

| Job | Does |
|---|---|
| `OutboxRelay` | Sends pending outbox records to the broker ([messaging](messaging.md#outbox)) |
| `MessagingJanitor` | Deletes old inbox marks and published outbox records |
| `AuditJanitor`, `AuditSealer` | Seals the audit trail and applies retention ([audit](audit.md)) |
| `IdempotencyJanitor` | Deletes expired idempotency records ([idempotency](idempotency.md)) |
| `DocumentJanitor` | Expires stale uploads, purges deleted content, removes orphan blobs |
| `TimerJob` | Runs due timed transitions: deadlines declared on resources ([timers](timers.md)) |
| `ProcessFeed` | Feeds the outbox to the process managers, exactly once, optionally also to a broker ([processes](processes.md)) |

Some of it is long-running: a `MessageConsumer` per queue. `JobRunner` runs
both kinds in one process.

Work that follows an operation, such as sending an email when an invoice is
approved, goes through events and consumers, not a task queue. The event is
committed with the change, so it is never sent for a change that rolled back
and never lost after one that committed.

## A worker

```python
# billing/worker.py
from datetime import timedelta

from resource_engine.audit import AuditJanitor, AuditSealer
from resource_engine.jobs import JobRunner
from resource_engine.messaging import MessageConsumer, MessagingJanitor, OutboxRelay
from resource_engine_sqlalchemy import SqlAlchemyLocks, locks_table


async def build() -> JobRunner:
    runner = JobRunner(locks=SqlAlchemyLocks(sessions, locks_table(metadata)))
    runner.add("outbox", OutboxRelay(uow_factory, publisher), every=1)
    runner.add("messaging-retention", MessagingJanitor(uow_factory), every=timedelta(hours=1))
    runner.add(
        "audit",
        AuditJanitor(uow_factory, retention=timedelta(days=3650), sealer=AuditSealer(uow_factory)),
        every=timedelta(minutes=10),
        single=True,
    )
    runner.add_service("billing", MessageConsumer(source, processor, concurrency=4))
    return runner
```

```toml
[tool.resource-engine]
worker = "billing.worker:build"
```

```bash
resource-engine worker            # until SIGTERM / Ctrl-C
resource-engine worker --once     # every job once, exit 1 if one failed (cron)
```

`worker` takes a `JobRunner`, or a function (sync or async) that builds one.
Pass `--runner module:attribute` to choose another. From your own code, call
`await runner.run_forever(stop)`; it stops when `stop` is set.

## Jobs

`runner.add(name, target, every=..., single=False, ttl=None, run_at_start=True)`

- **target:** an object with `run_once()`, or an async callable.
- **every:** seconds or a `timedelta`. The pause is between the end of one run
  and the start of the next, so runs never overlap within a worker.
- **Errors:** a failing run is logged and reported, then the job runs again on
  its next interval.
- **run_at_start:** whether the job runs as soon as the worker starts or waits
  for its first interval.

## Services

`runner.add_service(name, target)` runs `await target.run_forever(stop=event)`
until the worker stops. A service that crashes, or returns before the worker
stops, is logged and restarted after 5 seconds.

## One worker at a time

Scale workers out freely: the relay and consumers are safe with several
instances. Jobs that must not run twice at once take `single=True`. Before each
run, the job takes a lease named after it from the `LockProvider`. If another
worker holds the lease, the run is reported as `skipped`. The lease is released
after the run.

| Provider | Package | Notes |
|---|---|---|
| `SqlAlchemyLocks` | `resource-engine-sqlalchemy` | Lease table (`locks_table(metadata)`); a conditional `UPDATE` plus an `INSERT` guarded by the primary key. Any database. |
| `InMemoryLocks` | `resource-engine` (`resource_engine.testing`) | One process: tests, a single worker. |

- **Expiry:** a lease expires after `ttl` (10 minutes by default), so a worker
  that dies frees it.
- **Long runs:** keep `ttl` longer than a run. A run that outlasts its lease
  may overlap the next worker's.
- **Custom providers:** implement `acquire(name, owner, ttl) -> bool` and
  `release(name, owner)`. `LockContract` tests them.

Use `single=True` for `AuditSealer` and `AuditJanitor`. The janitors of the
outbox, inbox and documents are safe to run on several workers, but `single`
avoids doing the work twice.

## Metrics

`JobRunner(observers=[...])` callbacks get `(job, outcome, seconds, error)`
after every run. The outcome is `ok`, `failed` or `skipped`.
`resource_engine_opentelemetry.JobMetrics` records them as
`resource_engine.job.runs` and `resource_engine.job.duration` (see
[observability](observability.md#metrics)).

Log lines written during a run carry `job` in the log context, and
`resource-engine worker --log-format json` writes them as JSON
([logs](observability.md#logs)).

## In the explorer

`JobRunner(activity=activity_log)` reports to an `ActivityLog`:

- **Its jobs and their schedules:** registered at start and every
  `heartbeat`.
- **Every run:** recorded with its outcome.
- **"Run now":** it serves the requests the [explorer](explorer.md#jobs)
  records, through `run_job`, so `single` jobs keep their lease. A worker
  holding no lease runs nothing: the request ends `skipped`.

`runner.describe()` lists the jobs, `runner.register()` reports them, and
`runner.serve_requests()` runs the pending requests once (for tests and
external schedulers).

## Other schedulers

Every job has `run_once()`, so any scheduler can drive it instead of
`run_forever`:

- A Kubernetes CronJob, Cloud Scheduler, EventBridge Scheduler or plain cron
  can run `resource-engine worker --once`.
- An existing Celery beat or APScheduler can call `run_once()` from its task.

With several schedulers firing the same job, keep `single=True` jobs behind
the runner (`await runner.run_job("audit")`) so they still take the lease.
