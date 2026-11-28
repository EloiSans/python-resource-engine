"""The activity log in tables (``ActivityLog``), shared by the worker and the API.

Four tables, named ``<prefix>_<table>``: ``deliveries`` (each consumer's
handling of each message), ``job_runs``, ``job_requests`` ("run now") and
``workers`` (the jobs each worker runs, as a heartbeat).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from resource_engine.ports.activity import (
    DeliveryRecord,
    JobInfo,
    JobRunRecord,
    RequestStatus,
    RunRequest,
    WorkerJobs,
)
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DEFAULT_PREFIX = "resource_engine_activity"


@dataclass(frozen=True)
class ActivityTables:
    deliveries: Table
    job_runs: Table
    job_requests: Table
    workers: Table

    def all(self) -> list[Table]:
        return [self.deliveries, self.job_runs, self.job_requests, self.workers]


def activity_tables(metadata: MetaData, prefix: str = DEFAULT_PREFIX) -> ActivityTables:
    deliveries = Table(
        f"{prefix}_deliveries",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("consumer", String(200), nullable=False),
        Column("message_id", String(200), nullable=False),
        Column("event_type", String(200), nullable=False),
        Column("outcome", String(40), nullable=False),
        Column("seconds", Float, nullable=False),
        Column("error", String(1000)),
        Column("at", DateTime(timezone=True), nullable=False),
        Index(f"ix_{prefix}_deliveries_message", "message_id"),
        Index(f"ix_{prefix}_deliveries_at", "at"),
    )
    job_runs = Table(
        f"{prefix}_job_runs",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("job", String(200), nullable=False),
        Column("outcome", String(20), nullable=False),
        Column("seconds", Float, nullable=False),
        Column("owner", String(200), nullable=False),
        Column("error", String(1000)),
        Column("at", DateTime(timezone=True), nullable=False),
        Index(f"ix_{prefix}_job_runs_job", "job", "at"),
        Index(f"ix_{prefix}_job_runs_at", "at"),
    )
    job_requests = Table(
        f"{prefix}_job_requests",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("job", String(200), nullable=False),
        Column("requested_by", String(300)),
        Column("status", String(20), nullable=False),
        Column("requested_at", DateTime(timezone=True), nullable=False),
        Column("claimed_by", String(200)),
        Column("finished_at", DateTime(timezone=True)),
        Column("error", String(1000)),
        Index(f"ix_{prefix}_job_requests_status", "status", "requested_at"),
    )
    workers = Table(
        f"{prefix}_workers",
        metadata,
        Column("owner", String(200), primary_key=True),
        Column("jobs", Text, nullable=False),
        Column("seen_at", DateTime(timezone=True), nullable=False),
    )
    return ActivityTables(deliveries, job_runs, job_requests, workers)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _request(row: Any) -> RunRequest:
    return RunRequest(
        id=row.id,
        job=row.job,
        requested_by=row.requested_by,
        status=row.status,
        requested_at=_aware(row.requested_at),  # type: ignore[arg-type]
        claimed_by=row.claimed_by,
        finished_at=_aware(row.finished_at),
        error=row.error,
    )


class SqlAlchemyActivityLog:
    """``ActivityLog`` in its own short transactions."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], tables: ActivityTables
    ) -> None:
        self.session_factory = session_factory
        self.tables = tables

    # ---------------------------------------------------------------- jobs

    async def register_jobs(self, owner: str, jobs: Sequence[JobInfo]) -> None:
        t = self.tables.workers
        data = json.dumps(
            [
                {"name": j.name, "every": j.every, "single": j.single, "ttl": j.ttl,
                 "run_at_start": j.run_at_start}
                for j in jobs
            ]
        )  # fmt: skip
        async with self.session_factory() as session:
            await session.execute(delete(t).where(t.c.owner == owner))
            await session.execute(
                insert(t).values(owner=owner[:200], jobs=data, seen_at=datetime.now(UTC))
            )
            await session.commit()

    async def workers(self) -> list[WorkerJobs]:
        t = self.tables.workers
        async with self.session_factory() as session:
            rows = await session.execute(select(t).order_by(t.c.seen_at.desc()))
            return [
                WorkerJobs(
                    owner=r.owner,
                    jobs=tuple(JobInfo(**j) for j in json.loads(r.jobs)),
                    seen_at=_aware(r.seen_at),  # type: ignore[arg-type]
                )
                for r in rows
            ]

    async def record_job_run(self, run: JobRunRecord) -> None:
        async with self.session_factory() as session:
            await session.execute(
                insert(self.tables.job_runs).values(
                    id=run.id,
                    job=run.job[:200],
                    outcome=run.outcome,
                    seconds=run.seconds,
                    owner=run.owner[:200],
                    error=run.error[:1000] if run.error else None,
                    at=run.at,
                )
            )
            await session.commit()

    async def job_runs(self, job: str | None = None, limit: int = 50) -> list[JobRunRecord]:
        t = self.tables.job_runs
        query = select(t).order_by(t.c.at.desc(), t.c.id).limit(limit)
        if job is not None:
            query = query.where(t.c.job == job)
        async with self.session_factory() as session:
            return [
                JobRunRecord(
                    job=r.job,
                    outcome=r.outcome,
                    seconds=r.seconds,
                    owner=r.owner,
                    error=r.error,
                    at=_aware(r.at),  # type: ignore[arg-type]
                    id=r.id,
                )
                for r in await session.execute(query)
            ]

    async def request_run(self, job: str, requested_by: str | None) -> RunRequest:
        request = RunRequest(uuid.uuid4().hex, job, requested_by)
        async with self.session_factory() as session:
            await session.execute(
                insert(self.tables.job_requests).values(
                    id=request.id,
                    job=job[:200],
                    requested_by=(requested_by or None) and requested_by[:300],
                    status="pending",
                    requested_at=request.requested_at,
                )
            )
            await session.commit()
        return request

    async def run_request(self, request_id: str) -> RunRequest | None:
        t = self.tables.job_requests
        async with self.session_factory() as session:
            row = (await session.execute(select(t).where(t.c.id == request_id))).first()
            return _request(row) if row is not None else None

    async def claim_requests(
        self, owner: str, jobs: Collection[str], limit: int = 10
    ) -> list[RunRequest]:
        t = self.tables.job_requests
        if not jobs:
            return []
        claimed = []
        async with self.session_factory() as session:
            ids = [
                row[0]
                for row in await session.execute(
                    select(t.c.id)
                    .where(t.c.status == "pending", t.c.job.in_(list(jobs)))
                    .order_by(t.c.requested_at)
                    .limit(limit)
                )
            ]
            for request_id in ids:
                # compare-and-set: another worker may claim the same row first
                result = await session.execute(
                    update(t)
                    .where(t.c.id == request_id, t.c.status == "pending")
                    .values(status="running", claimed_by=owner[:200])
                )
                if result.rowcount == 1:  # type: ignore[attr-defined]
                    claimed.append(request_id)
            await session.commit()
            if not claimed:
                return []
            rows = await session.execute(
                select(t).where(t.c.id.in_(claimed)).order_by(t.c.requested_at)
            )
            return [_request(r) for r in rows]

    async def finish_request(
        self, request_id: str, status: RequestStatus, error: str | None = None
    ) -> None:
        t = self.tables.job_requests
        async with self.session_factory() as session:
            await session.execute(
                update(t)
                .where(t.c.id == request_id)
                .values(
                    status=status,
                    error=error[:1000] if error else None,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    # ---------------------------------------------------------- deliveries

    async def record_delivery(self, record: DeliveryRecord) -> None:
        async with self.session_factory() as session:
            await session.execute(
                insert(self.tables.deliveries).values(
                    id=record.id,
                    consumer=record.consumer[:200],
                    message_id=record.message_id[:200],
                    event_type=record.event_type[:200],
                    outcome=record.outcome,
                    seconds=record.seconds,
                    error=record.error[:1000] if record.error else None,
                    at=record.at,
                )
            )
            await session.commit()

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        t = self.tables.deliveries
        async with self.session_factory() as session:
            rows = await session.execute(
                select(t).where(t.c.message_id == message_id).order_by(t.c.at, t.c.id)
            )
            return [
                DeliveryRecord(
                    consumer=r.consumer,
                    message_id=r.message_id,
                    event_type=r.event_type,
                    outcome=r.outcome,
                    seconds=r.seconds,
                    error=r.error,
                    at=_aware(r.at),  # type: ignore[arg-type]
                    id=r.id,
                )
                for r in rows
            ]

    # ----------------------------------------------------------- retention

    async def purge(self, before: datetime, limit: int) -> int:
        t = self.tables
        targets = [
            (t.deliveries, t.deliveries.c.id, t.deliveries.c.at),
            (t.job_runs, t.job_runs.c.id, t.job_runs.c.at),
            (t.job_requests, t.job_requests.c.id, t.job_requests.c.finished_at),
            (t.workers, t.workers.c.owner, t.workers.c.seen_at),
        ]
        total = 0
        async with self.session_factory() as session:
            for table, key, when in targets:
                left = limit - total
                if left <= 0:
                    break
                keys = [
                    row[0]
                    for row in await session.execute(
                        select(key)
                        .where(when.is_not(None), when < before)
                        .order_by(when)
                        .limit(left)
                    )
                ]
                if keys:
                    await session.execute(delete(table).where(key.in_(keys)))
                    total += len(keys)
            await session.commit()
        return total


__all__ = ["ActivityTables", "SqlAlchemyActivityLog", "activity_tables"]
