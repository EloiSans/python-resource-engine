"""Activity log port: what the worker did, for the explorer and operators.

The worker and the API are separate processes, so what one does and the
other shows goes through a store both reach:

- which consumers handled each event and how (``record_delivery``, fed by
  ``ActivityMessageObserver``);
- the worker's jobs and their schedules (``register_jobs``), their runs
  (``record_job_run``, fed by ``JobRunner(activity=...)``), and "run now"
  requests the API records and the worker claims (``request_run``).

Adapters: ``InMemoryActivityLog`` (tests, one process) and
``SqlAlchemyActivityLog``. ``MessagingJanitor(activity_retention=)`` purges it.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

type RequestStatus = Literal["pending", "running", "ok", "failed", "skipped"]


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class DeliveryRecord:
    """One consumer's handling of one message.

    Attributes:
        outcome: ``ack``, ``duplicate``, ``retry``, ``dead_letter``... (the
            ``MessageProcessor`` outcome).
        error: Code and message of the failure, if any.
    """

    consumer: str
    message_id: str
    event_type: str
    outcome: str
    seconds: float
    error: str | None = None
    at: datetime = field(default_factory=_now)
    id: str = field(default_factory=_id)


@dataclass(frozen=True)
class JobInfo:
    """A job as a worker runs it (``JobRunner.describe``)."""

    name: str
    every: float
    single: bool = False
    ttl: float = 600.0
    run_at_start: bool = True


@dataclass(frozen=True)
class WorkerJobs:
    """The jobs one worker runs, as it last said (``seen_at``)."""

    owner: str
    jobs: tuple[JobInfo, ...]
    seen_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class JobRunRecord:
    """One run of a job: ``ok``, ``failed`` or ``skipped`` (another worker
    held the lease of a ``single`` job)."""

    job: str
    outcome: str
    seconds: float
    owner: str
    error: str | None = None
    at: datetime = field(default_factory=_now)
    id: str = field(default_factory=_id)


@dataclass(frozen=True)
class RunRequest:
    """Someone asked for a job to run now. The worker claims it (``running``)
    and runs it through its runner, so leases hold; then ``ok``, ``failed``,
    or ``skipped`` when another worker held the lease."""

    id: str
    job: str
    requested_by: str | None
    status: RequestStatus = "pending"
    requested_at: datetime = field(default_factory=_now)
    claimed_by: str | None = None
    finished_at: datetime | None = None
    error: str | None = None


class ActivityLog(Protocol):
    async def register_jobs(self, owner: str, jobs: Sequence[JobInfo]) -> None:
        """Replace what ``owner`` runs (called at start, then as a heartbeat)."""
        ...

    async def workers(self) -> list[WorkerJobs]:
        """Every worker that registered, most recently seen first."""
        ...

    async def record_job_run(self, run: JobRunRecord) -> None: ...

    async def job_runs(self, job: str | None = None, limit: int = 50) -> list[JobRunRecord]:
        """Runs (of ``job``), newest first."""
        ...

    async def request_run(self, job: str, requested_by: str | None) -> RunRequest: ...

    async def run_request(self, request_id: str) -> RunRequest | None: ...

    async def claim_requests(
        self, owner: str, jobs: Collection[str], limit: int = 10
    ) -> list[RunRequest]:
        """Mark up to ``limit`` pending requests for ``jobs`` as ``running`` by
        ``owner`` and return them; a request is claimed by one worker only."""
        ...

    async def finish_request(
        self, request_id: str, status: RequestStatus, error: str | None = None
    ) -> None: ...

    async def record_delivery(self, record: DeliveryRecord) -> None: ...

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        """Every consumer's handling of ``message_id``, oldest first."""
        ...

    async def purge(self, before: datetime, limit: int) -> int:
        """Delete up to ``limit`` records (deliveries, runs, finished requests,
        workers not seen since) older than ``before``; returns how many."""
        ...


__all__ = [
    "ActivityLog",
    "DeliveryRecord",
    "JobInfo",
    "JobRunRecord",
    "RequestStatus",
    "RunRequest",
    "WorkerJobs",
]
