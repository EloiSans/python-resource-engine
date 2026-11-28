"""Table export: a resource's list as CSV or XLSX, with the caller's filters,
sorting, permissions, hidden fields and tenant, exactly as the API lists it.

```python
from resource_engine.exports import export_table

content, media_type, filename = await export_table(
    manager, "invoices", ListQuery(filters={"status": "approved"}), format="xlsx", actor=user
)
```

Rows are read page by page through ``manager.list`` as the caller, so a user
exports what they could list and nothing else: rows outside their row filter
or tenant aren't there, and hidden fields are empty. ``max_rows`` (10 000 by
default) caps an export; above it, narrow the filters. Over HTTP:
``GET /<resource>/export?format=csv|xlsx`` with the list's filters
(``mount_resources``). XLSX needs ``resource-engine[xlsx]`` (openpyxl).

**Large exports** run in the background (``ExportJobs``, available as
``manager.exports`` when a blob store is configured): the request checks the
permission and the query and returns at once; a worker job writes the file to
the blob store, where the requester downloads it until it expires:

```python
job = await manager.exports.request("invoices", query, format="xlsx", actor=user)
runner.add("exports", manager.exports, every=5, single=True)     # the worker
job = await manager.exports.get(job.id, actor=user)               # pending, running, done, failed
meta, stream = await manager.exports.open(job.id, actor=user)
```

Over HTTP: ``POST /<resource>/exports`` (202), ``GET /exports/{id}`` and
``GET /exports/{id}/content``. See docs/exports.md.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import io
import json
import logging
import re
import secrets
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from resource_engine.application.query import ListQuery
from resource_engine.errors import (
    BlobNotFound,
    ConfigurationError,
    DocumentNotAvailable,
    EntityNotFound,
    InvalidQuery,
    ResourceEngineError,
)
from resource_engine.ports.identity import Principal
from resource_engine.ports.storage import BlobStore, PresignedRequest, SupportsPresignedUrls

log = logging.getLogger("resource_engine.exports")

type ExportFormat = Literal["csv", "xlsx"]

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def columns_of(manager: Any, resource: str) -> list[str]:
    """The export's columns: the read schema's fields, in order."""
    definition = manager.registry.get(resource)
    read = definition.schemas.get("read")
    return list(read.model_fields) if read is not None else []


async def iter_rows(
    manager: Any,
    resource: str,
    query: ListQuery | None = None,
    *,
    actor: Any = None,
    tenant: str | None = None,
    page_size: int = 500,
    max_rows: int = 10_000,
) -> AsyncIterator[dict[str, Any]]:
    """The caller's rows as dicts (hidden fields left out), page by page."""
    from resource_engine.authz.fields import hidden_fields

    query = query or ListQuery()
    page_size = min(page_size, manager.registry.get(resource).max_page_size)
    offset, sent = 0, 0
    while True:
        page_query = ListQuery(
            offset=offset,
            limit=page_size,
            order_by=list(query.order_by),
            filters=dict(query.filters),
        )
        page = await manager.list(resource, page_query, actor=actor, tenant=tenant)
        if offset == 0 and page.total > max_rows:
            raise InvalidQuery(
                f"{page.total} {resource} match; an export is limited to {max_rows} rows",
                details={"total": page.total, "max_rows": max_rows},
                hint="Narrow the filters, or export in parts.",
            )
        for item in page.items:
            if isinstance(item, BaseModel):
                yield item.model_dump(exclude=set(hidden_fields(item)))
            else:
                yield dict(vars(item))
            sent += 1
        offset += page_size
        if len(page.items) < page_size or sent >= page.total:
            return


def _cell(value: Any, *, native: bool) -> Any:
    """A value for a cell: numbers and dates stay native in XLSX; lists and
    objects become JSON; ``None`` is empty."""
    if value is None:
        return "" if not native else None
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return value if native else ("true" if value else "false")
    if isinstance(value, int | float | Decimal):
        return value if native else str(value)
    if isinstance(value, datetime):
        if native:  # Excel has no time zones: UTC, without the offset
            return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        return value.isoformat()
    if isinstance(value, date):
        return value if native else value.isoformat()
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, default=str, ensure_ascii=False)


async def stream_csv(
    rows: AsyncIterator[dict[str, Any]],
    columns: Sequence[str],
    *,
    delimiter: str = ",",
    excel: bool = True,
    batch: int = 500,
) -> AsyncIterator[bytes]:
    """CSV with a header row, as UTF-8 chunks of ``batch`` rows. ``excel`` adds
    a byte order mark so Excel opens accented text correctly."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    if excel:
        out.write("\ufeff")
    writer.writerow(columns)
    count = 0
    async for row in rows:
        writer.writerow([_cell(row.get(c), native=False) for c in columns])
        count += 1
        if count % batch == 0:
            yield out.getvalue().encode("utf-8")
            out.seek(0)
            out.truncate()
    if out.tell():
        yield out.getvalue().encode("utf-8")


async def write_csv(
    rows: AsyncIterator[dict[str, Any]],
    columns: Sequence[str],
    *,
    delimiter: str = ",",
    excel: bool = True,
) -> bytes:
    """CSV with a header row (see ``stream_csv``), in memory."""
    parts = [part async for part in stream_csv(rows, columns, delimiter=delimiter, excel=excel)]
    return b"".join(parts)


async def _workbook(rows: AsyncIterator[dict[str, Any]], columns: Sequence[str], sheet: str) -> Any:
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font
    except ImportError:  # pragma: no cover - depends on the extra
        raise InvalidQuery(
            "XLSX export needs openpyxl",
            hint="Install resource-engine[xlsx], or export as CSV.",
        ) from None
    book = Workbook(write_only=True)
    page = book.create_sheet(sheet[:31] or "export")
    page.freeze_panes = "A2"
    bold = Font(bold=True)
    header = []
    for name in columns:
        cell = WriteOnlyCell(page, value=name)
        cell.font = bold
        header.append(cell)
    page.append(header)
    async for row in rows:
        page.append([_cell(row.get(c), native=True) for c in columns])
    return book


async def write_xlsx(
    rows: AsyncIterator[dict[str, Any]], columns: Sequence[str], *, sheet: str = "export"
) -> bytes:
    """An XLSX workbook (one sheet, header row frozen), written in openpyxl's
    streaming mode."""
    book = await _workbook(rows, columns, sheet)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


async def stream_xlsx(
    rows: AsyncIterator[dict[str, Any]],
    columns: Sequence[str],
    *,
    sheet: str = "export",
    chunk: int = 256 * 1024,
) -> AsyncIterator[bytes]:
    """An XLSX workbook saved to a temporary file (spilled to disk past 8 MB)
    and streamed from it, for exports too large to hold in memory."""
    book = await _workbook(rows, columns, sheet)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
        book.save(tmp)  # same thread: openpyxl's write-only sheets can't move between threads
        tmp.seek(0)
        while part := tmp.read(chunk):
            yield part


async def export_table(
    manager: Any,
    resource: str,
    query: ListQuery | None = None,
    *,
    format: ExportFormat = "csv",
    actor: Any = None,
    tenant: str | None = None,
    max_rows: int = 10_000,
    columns: Sequence[str] | None = None,
) -> tuple[bytes, str, str]:
    """``(content, media type, file name)`` of a resource's list."""
    if format not in MEDIA_TYPES:
        raise InvalidQuery(f"Unknown export format {format!r}; use csv or xlsx")
    names = list(columns or columns_of(manager, resource))
    rows = iter_rows(manager, resource, query, actor=actor, tenant=tenant, max_rows=max_rows)
    content = (
        await write_xlsx(rows, names, sheet=resource)
        if format == "xlsx"
        else await write_csv(rows, names)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return content, MEDIA_TYPES[format], f"{resource}-{stamp}.{format}"


# --------------------------------------------------------- background exports

type ExportStatus = Literal["pending", "running", "done", "failed"]

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def dump_actor(actor: Any) -> Any:
    """The requester, as JSON, for the worker to export as them: ``None``,
    a string or a ``Principal`` (roles, tenant and claims as they were when
    the export was requested). Other actor types need ``ExportJobs(dump_actor=,
    load_actor=)``."""
    from resource_engine.authz.evaluator import SystemPrincipal

    if actor is None or isinstance(actor, str):
        return actor
    if isinstance(actor, SystemPrincipal):
        return {"system": actor.subject}
    if isinstance(actor, Principal):
        data = dataclasses.asdict(actor)
        for name in ("roles", "groups", "scopes"):
            data[name] = sorted(data[name])
        data["claims"] = json.loads(json.dumps(data["claims"], default=str))
        return {"principal": data}
    raise ConfigurationError(
        f"Background exports can't store a {type(actor).__name__} actor",
        hint="Pass ExportJobs(dump_actor=..., load_actor=...) for this actor type.",
        doc="docs/exports.md#background-exports",
    )


def load_actor(data: Any) -> Any:
    if isinstance(data, Mapping) and "system" in data:
        from resource_engine.authz.base import system_principal

        return system_principal(str(data["system"]))
    if isinstance(data, Mapping) and "principal" in data:
        values = dict(data["principal"])
        for name in ("roles", "groups", "scopes"):
            values[name] = frozenset(values.get(name) or ())
        return Principal(**values)
    return data


def _actor_label(actor: Any) -> str | None:
    from resource_engine.audit.recorder import actor_id

    if actor is None or isinstance(actor, str):
        return actor
    return actor_id(actor) or str(actor)


def _now() -> datetime:
    return datetime.now(UTC)


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class ExportJob:
    """A background export and where it is.

    Attributes:
        status: ``pending`` (waiting for the worker), ``running``, ``done``
            (the file can be downloaded until ``expires_at``) or ``failed``
            (``error`` says why).
        rows: How many rows matched when the export was requested.
        requested_by: The requester; only they can see and download it.
    """

    id: str
    resource: str
    format: ExportFormat
    filters: dict[str, Any] = field(default_factory=dict)
    order_by: list[str] = field(default_factory=list)
    tenant: str | None = None
    requested_by: str | None = None
    actor: Any = None
    status: ExportStatus = "pending"
    rows: int | None = None
    created_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    attempts: int = 0
    filename: str | None = None
    size: int | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Everything but the stored actor: what the API shows."""
        data = {
            "id": self.id,
            "resource": self.resource,
            "format": self.format,
            "status": self.status,
            "rows": self.rows,
            "filename": self.filename,
            "size": self.size,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "error": self.error,
        }
        return data

    def _manifest(self) -> bytes:
        data = self.to_dict()
        data.update(
            filters=self.filters,
            order_by=self.order_by,
            tenant=self.tenant,
            requested_by=self.requested_by,
            actor=self.actor,
            attempts=self.attempts,
        )
        return json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")

    @classmethod
    def _load(cls, raw: bytes) -> ExportJob:
        data = json.loads(raw)
        return cls(
            id=data["id"],
            resource=data["resource"],
            format=data["format"],
            filters=data.get("filters") or {},
            order_by=list(data.get("order_by") or ()),
            tenant=data.get("tenant"),
            requested_by=data.get("requested_by"),
            actor=data.get("actor"),
            status=data["status"],
            rows=data.get("rows"),
            created_at=_time(data["created_at"]) or _now(),
            started_at=_time(data.get("started_at")),
            finished_at=_time(data.get("finished_at")),
            expires_at=_time(data.get("expires_at")),
            attempts=int(data.get("attempts") or 0),
            filename=data.get("filename"),
            size=data.get("size"),
            error=data.get("error"),
        )


class ExportJobs:
    """Exports too large to answer in one request, written by a worker job.

    Each export is two objects in the blob store: ``<prefix><id>.json`` (the
    job: query, status, the requester) and ``<prefix><id>.<format>`` (the
    file). Nothing else is needed: no table, no broker.

    Args:
        manager: The ``ResourceManager`` the rows are read through.
        store: Where jobs and files go (the documents' blob store by default).
        prefix: Key prefix of the jobs and files.
        max_rows: The most rows one background export may have.
        retention: How long a finished export (and its file) is kept.
        stale_after: A ``running`` job not finished after this long (its
            worker died) runs again, up to ``max_attempts`` times.
        dump_actor / load_actor: How the requester is stored for the worker.
        path: URL segment of the HTTP routes (``/exports/{id}``).

    Run it as a job: ``runner.add("exports", manager.exports, every=5,
    single=True)``. Rows are read as the requester, with the roles they had
    when they asked: someone who loses access after asking still gets the
    file, within ``retention``.
    """

    def __init__(
        self,
        manager: Any,
        store: BlobStore,
        *,
        prefix: str = "exports/",
        max_rows: int = 1_000_000,
        retention: timedelta = timedelta(days=1),
        stale_after: timedelta = timedelta(minutes=30),
        max_attempts: int = 3,
        download_ttl: timedelta = timedelta(minutes=15),
        dump_actor: Callable[[Any], Any] = dump_actor,
        load_actor: Callable[[Any], Any] = load_actor,
        path: str = "exports",
    ) -> None:
        self.manager = manager
        self.store = store
        self.prefix = prefix
        self.max_rows = max_rows
        self.retention = retention
        self.stale_after = stale_after
        self.max_attempts = max_attempts
        self.download_ttl = download_ttl
        self.dump_actor = dump_actor
        self.load_actor = load_actor
        self.path = path.strip("/")

    # ------------------------------------------------------------- storage

    def _job_key(self, job_id: str) -> str:
        return f"{self.prefix}{job_id}.json"

    def _file_key(self, job: ExportJob) -> str:
        return f"{self.prefix}{job.id}.{job.format}"

    async def _save(self, job: ExportJob) -> None:
        await self.store.put(
            self._job_key(job.id), job._manifest(), content_type="application/json"
        )

    async def _load(self, job_id: str) -> ExportJob | None:
        if not _JOB_ID.match(job_id):
            return None
        try:
            raw = b"".join([part async for part in self.store.open(self._job_key(job_id))])
        except BlobNotFound:
            return None
        return ExportJob._load(raw)

    # ------------------------------------------------------------ requests

    async def request(
        self,
        resource: str,
        query: ListQuery | None = None,
        *,
        format: ExportFormat = "csv",
        actor: Any = None,
        tenant: str | None = None,
    ) -> ExportJob:
        """Check that ``actor`` may list ``resource`` with ``query`` and queue
        the export. Raises what the list would (permission, invalid query)
        and ``InvalidQuery`` past ``max_rows``."""
        if format not in MEDIA_TYPES:
            raise InvalidQuery(f"Unknown export format {format!r}; use csv or xlsx")
        query = query or ListQuery()
        probe = ListQuery(
            offset=0, limit=1, order_by=list(query.order_by), filters=dict(query.filters)
        )
        page = await self.manager.list(resource, probe, actor=actor, tenant=tenant)
        if page.total > self.max_rows:
            raise InvalidQuery(
                f"{page.total} {resource} match; an export is limited to {self.max_rows} rows",
                details={"total": page.total, "max_rows": self.max_rows},
                hint="Narrow the filters, or export in parts.",
            )
        job = ExportJob(
            id=secrets.token_hex(16),
            resource=resource,
            format=format,
            filters=json.loads(json.dumps(dict(query.filters), default=str)),
            order_by=list(query.order_by),
            tenant=tenant,
            requested_by=_actor_label(actor),
            actor=self.dump_actor(actor),
            rows=page.total,
        )
        await self._save(job)
        log.info(
            "export requested",
            extra={"export_id": job.id, "resource": resource, "rows": page.total},
        )
        return job

    async def get(self, job_id: str, *, actor: Any = None) -> ExportJob:
        """The export ``job_id`` of ``actor`` (``EntityNotFound`` for anyone else)."""
        job = await self._load(job_id)
        if job is None or job.requested_by != _actor_label(actor):
            raise EntityNotFound(f"Export {job_id!r} does not exist")
        return job

    async def _ready(self, job_id: str, actor: Any) -> ExportJob:
        job = await self.get(job_id, actor=actor)
        if job.status != "done":
            raise DocumentNotAvailable(
                f"Export {job_id!r} is {job.status}",
                hint="Wait until its status is done.",
                details={"status": job.status, "error": job.error},
            )
        return job

    async def open(
        self, job_id: str, *, actor: Any = None
    ) -> tuple[ExportJob, AsyncIterator[bytes]]:
        """The finished export and a stream of its file."""
        job = await self._ready(job_id, actor)
        return job, self.store.open(self._file_key(job))

    async def download_url(self, job_id: str, *, actor: Any = None) -> PresignedRequest:
        if not isinstance(self.store, SupportsPresignedUrls):
            raise ConfigurationError(f"{type(self.store).__name__} cannot presign downloads")
        job = await self._ready(job_id, actor)
        return await self.store.presign_get(
            self._file_key(job), expires=self.download_ttl, download_name=job.filename
        )

    # -------------------------------------------------------------- worker

    async def __call__(self) -> int:
        return await self.run_once()

    async def jobs(self) -> list[ExportJob]:
        """Every stored export, oldest first."""
        found = []
        async for info in self.store.list(self.prefix):
            name = info.key[len(self.prefix) :]
            if name.endswith(".json") and _JOB_ID.match(name.removesuffix(".json")):
                job = await self._load(name.removesuffix(".json"))
                if job is not None:
                    found.append(job)
        found.sort(key=lambda j: j.created_at)
        return found

    async def run_once(self, *, limit: int = 5) -> int:
        """Write up to ``limit`` waiting exports and delete expired ones.
        Returns how many exports were written."""
        now = _now()
        written = 0
        for job in await self.jobs():
            if job.expires_at is not None and job.expires_at <= now:
                await self.store.delete([self._file_key(job), self._job_key(job.id)])
                continue
            if job.status == "pending" and job.created_at + self.retention <= now:
                job.status, job.finished_at, job.expires_at = "failed", now, now + self.retention
                job.error = {"code": "RE-E000", "message": "No worker ran the export in time"}
                await self._save(job)
                continue
            stale = (
                job.status == "running"
                and job.started_at is not None
                and job.started_at + self.stale_after <= now
            )
            if written < limit and (job.status == "pending" or stale):
                await self.run(job)
                written += 1
        return written

    async def run(self, job: ExportJob) -> ExportJob:
        """Write one export's file, as its requester."""
        from resource_engine.reporting import report_error

        job.attempts += 1
        if job.attempts > self.max_attempts:
            return await self._fail(job, {"code": "RE-E000", "message": "The export kept failing"})
        job.status, job.started_at = "running", _now()
        await self._save(job)
        actor = self.load_actor(job.actor)
        query = ListQuery(order_by=list(job.order_by), filters=dict(job.filters))
        names = columns_of(self.manager, job.resource)
        rows = iter_rows(
            self.manager,
            job.resource,
            query,
            actor=actor,
            tenant=job.tenant,
            max_rows=self.max_rows,
        )
        content = (
            stream_xlsx(rows, names, sheet=job.resource)
            if job.format == "xlsx"
            else stream_csv(rows, names)
        )
        try:
            info = await self.store.put(
                self._file_key(job), content, content_type=MEDIA_TYPES[job.format].split(";")[0]
            )
        except ResourceEngineError as exc:
            await content.aclose()
            return await self._fail(job, {"code": exc.code, "message": exc.message})
        except Exception as exc:
            await content.aclose()
            await report_error(exc, where="exports.run", actor=actor, export_id=job.id)
            return await self._fail(job, {"code": "RE-E000", "message": "Internal error"})
        stamp = job.created_at.strftime("%Y%m%d-%H%M")
        job.status, job.finished_at, job.size = "done", _now(), info.size
        job.filename = f"{job.resource}-{stamp}.{job.format}"
        job.expires_at = job.finished_at + self.retention
        job.error = None
        await self._save(job)
        log.info("export written", extra={"export_id": job.id, "bytes": info.size})
        return job

    async def _fail(self, job: ExportJob, error: dict[str, Any]) -> ExportJob:
        job.status, job.finished_at, job.error = "failed", _now(), error
        job.expires_at = job.finished_at + self.retention
        await self._save(job)
        log.warning("export failed", extra={"export_id": job.id, "code": error.get("code")})
        return job


__all__ = [
    "MEDIA_TYPES",
    "ExportJob",
    "ExportJobs",
    "columns_of",
    "dump_actor",
    "export_table",
    "iter_rows",
    "load_actor",
    "stream_csv",
    "stream_xlsx",
    "write_csv",
    "write_xlsx",
]
