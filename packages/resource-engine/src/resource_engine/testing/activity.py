"""``ActivityLog`` in this process (tests, single-process apps)."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime

from resource_engine.ports.activity import (
    DeliveryRecord,
    JobInfo,
    JobRunRecord,
    RequestStatus,
    RunRequest,
    WorkerJobs,
)


class InMemoryActivityLog:
    def __init__(self) -> None:
        self.delivered: list[DeliveryRecord] = []
        self.runs: list[JobRunRecord] = []
        self.requests: dict[str, RunRequest] = {}
        self.registered: dict[str, WorkerJobs] = {}

    # ---------------------------------------------------------------- jobs

    async def register_jobs(self, owner: str, jobs: Sequence[JobInfo]) -> None:
        self.registered[owner] = WorkerJobs(owner, tuple(jobs))

    async def workers(self) -> list[WorkerJobs]:
        return sorted(self.registered.values(), key=lambda w: w.seen_at, reverse=True)

    async def record_job_run(self, run: JobRunRecord) -> None:
        self.runs.append(run)

    async def job_runs(self, job: str | None = None, limit: int = 50) -> list[JobRunRecord]:
        found = [r for r in self.runs if job is None or r.job == job]
        return sorted(found, key=lambda r: r.at, reverse=True)[:limit]

    async def request_run(self, job: str, requested_by: str | None) -> RunRequest:
        request = RunRequest(uuid.uuid4().hex, job, requested_by)
        self.requests[request.id] = request
        return request

    async def run_request(self, request_id: str) -> RunRequest | None:
        return self.requests.get(request_id)

    async def claim_requests(
        self, owner: str, jobs: Collection[str], limit: int = 10
    ) -> list[RunRequest]:
        pending = sorted(
            (r for r in self.requests.values() if r.status == "pending" and r.job in jobs),
            key=lambda r: r.requested_at,
        )[:limit]
        claimed = [dataclasses.replace(r, status="running", claimed_by=owner) for r in pending]
        for request in claimed:
            self.requests[request.id] = request
        return claimed

    async def finish_request(
        self, request_id: str, status: RequestStatus, error: str | None = None
    ) -> None:
        request = self.requests.get(request_id)
        if request is not None:
            self.requests[request_id] = dataclasses.replace(
                request, status=status, error=error, finished_at=datetime.now(UTC)
            )

    # ---------------------------------------------------------- deliveries

    async def record_delivery(self, record: DeliveryRecord) -> None:
        self.delivered.append(record)

    async def deliveries(self, message_id: str) -> list[DeliveryRecord]:
        return sorted((r for r in self.delivered if r.message_id == message_id), key=lambda r: r.at)

    async def purge(self, before: datetime, limit: int) -> int:
        count = 0
        for record in [r for r in self.delivered if r.at < before][:limit]:
            self.delivered.remove(record)
            count += 1
        for run in [r for r in self.runs if r.at < before][: limit - count]:
            self.runs.remove(run)
            count += 1
        finished = [
            r
            for r in self.requests.values()
            if r.finished_at is not None and r.finished_at < before
        ][: limit - count]
        for request in finished:
            del self.requests[request.id]
            count += 1
        for owner in [w.owner for w in self.registered.values() if w.seen_at < before][
            : limit - count
        ]:
            del self.registered[owner]
            count += 1
        return count


__all__ = ["InMemoryActivityLog"]
