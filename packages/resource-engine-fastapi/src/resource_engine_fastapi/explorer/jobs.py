"""The explorer's jobs: what the workers run, their runs, "run now", and
the timed transitions waiting on a record.

- ``GET <api>/jobs``: every job the workers registered, with its schedule
  and last run (``jobs:read``).
- ``GET <api>/jobs/{name}/runs``: its recent runs (``jobs:read``).
- ``POST <api>/jobs/{name}/run``: ask a worker to run it now (``jobs:run``,
  audited). The worker claims the request and runs the job through its
  runner, so a ``single`` job's lease holds.
- ``GET <api>/jobs/requests/{id}``: that request's status (``jobs:read``).
- ``GET <api>/timers/{resource}/{id}``: the record's pending timed
  transitions (``get`` on the record).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from resource_engine.application.timers import pending_timers
from resource_engine.errors import EntityNotFound
from resource_engine.explorer import JOBS_READ, JOBS_RUN, require


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _run(run: Any) -> dict[str, Any]:
    return {
        "job": run.job,
        "outcome": run.outcome,
        "seconds": run.seconds,
        "owner": run.owner,
        "error": run.error,
        "at": _iso(run.at),
    }


def _request(request: Any) -> dict[str, Any]:
    return {
        "id": request.id,
        "job": request.job,
        "status": request.status,
        "requested_by": request.requested_by,
        "requested_at": _iso(request.requested_at),
        "claimed_by": request.claimed_by,
        "finished_at": _iso(request.finished_at),
        "error": request.error,
    }


def build_jobs_router(
    manager: Any, activity: Any, *, actor_dependency: Any, open: bool = False
) -> APIRouter:
    router = APIRouter(tags=["explorer"])

    async def known_jobs() -> dict[str, dict[str, Any]]:
        jobs: dict[str, dict[str, Any]] = {}
        for worker in await activity.workers():
            for info in worker.jobs:
                entry = jobs.setdefault(
                    info.name,
                    {
                        "name": info.name,
                        "every": info.every,
                        "single": info.single,
                        "ttl": info.ttl,
                        "run_at_start": info.run_at_start,
                        "workers": [],
                    },
                )
                entry["workers"].append(worker.owner)
        return jobs

    @router.get("/jobs", summary="The workers' jobs and their last runs",
                operation_id="explorer_jobs")  # fmt: skip
    async def jobs(actor: Any = Depends(actor_dependency)) -> dict[str, Any]:
        await require(manager, actor, JOBS_READ.name, open=open)
        known = await known_jobs()
        for name, entry in known.items():
            last = await activity.job_runs(name, limit=1)
            entry["last_run"] = _run(last[0]) if last else None
        return {
            "workers": [
                {"owner": w.owner, "seen_at": _iso(w.seen_at), "jobs": [j.name for j in w.jobs]}
                for w in await activity.workers()
            ],
            "jobs": sorted(known.values(), key=lambda j: j["name"]),
        }

    @router.get("/jobs/requests/{request_id}", summary="A run-now request",
                operation_id="explorer_job_request")  # fmt: skip
    async def job_request(request_id: str, actor: Any = Depends(actor_dependency)) -> Any:
        await require(manager, actor, JOBS_READ.name, open=open)
        request = await activity.run_request(request_id)
        if request is None:
            raise EntityNotFound(f"Run request {request_id!r} does not exist")
        return _request(request)

    @router.get("/jobs/{name}/runs", summary="A job's recent runs",
                operation_id="explorer_job_runs")  # fmt: skip
    async def job_runs(
        name: str,
        limit: int = Query(20, ge=1, le=200),
        actor: Any = Depends(actor_dependency),
    ) -> list[dict[str, Any]]:
        await require(manager, actor, JOBS_READ.name, open=open)
        return [_run(r) for r in await activity.job_runs(name, limit=limit)]

    @router.post(
        "/jobs/{name}/run",
        summary="Ask a worker to run a job now",
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="explorer_run_job",
    )
    async def run_job(name: str, actor: Any = Depends(actor_dependency)) -> JSONResponse:
        await require(manager, actor, JOBS_RUN.name, open=open)
        if name not in await known_jobs():
            raise EntityNotFound(
                f"No worker runs a job named {name!r}",
                hint="GET /jobs lists the jobs the running workers registered.",
            )
        from resource_engine.audit.recorder import actor_id

        who = actor if isinstance(actor, str) else actor_id(actor)
        request = await activity.request_run(name, who)
        await manager.audit_event(
            actor, JOBS_RUN.name, details={"job": name, "request": request.id}
        )
        return JSONResponse(_request(request), status_code=status.HTTP_202_ACCEPTED)

    @router.get(
        "/timers/{resource}/{entity_id}",
        summary="The timed transitions waiting on a record",
        operation_id="explorer_record_timers",
    )
    async def timers(
        resource: str,
        entity_id: str,
        tenant: str | None = Query(None),
        actor: Any = Depends(actor_dependency),
    ) -> list[dict[str, Any]]:
        from pydantic import TypeAdapter

        definition = manager.registry.get(resource)
        typed = TypeAdapter(definition.id_type).validate_python(entity_id)
        found = await pending_timers(manager, resource, typed, actor=actor, tenant=tenant)
        return [
            {
                "event": t.event,
                "due_at": _iso(t.due_at),
                "overdue": t.overdue,
                "since": t.since,
                "after_seconds": t.after.total_seconds(),
                "states": [str(getattr(s, "value", s)) for s in t.states],
                "description": t.description,
            }
            for t in found
        ]

    return router


__all__ = ["build_jobs_router"]
