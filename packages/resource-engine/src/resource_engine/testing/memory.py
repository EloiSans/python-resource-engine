from __future__ import annotations

import asyncio
import copy
import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from resource_engine.application.query import ListQuery, Page
from resource_engine.domain.events import DomainEvent
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import OutboxRecord, ReceivedMessage

OUTBOX_TABLE = "__outbox__"
INBOX_TABLE = "__inbox__"
IDEMPOTENCY_TABLE = "__idempotency__"

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry


class InMemoryStore:
    """Process-local storage shared by the units of work created from it.

    Each unit of work works on a deep copy and swaps it in on commit, so rollback
    is simply discarding the copy. Not meant for concurrent production use.
    """

    def __init__(
        self,
        id_fields: dict[str, str] | None = None,
        version_fields: dict[str, str] | None = None,
        families: dict[str, tuple[str, str, str]] | None = None,
        views: dict[str, tuple[Any, Any]] | None = None,
    ) -> None:
        self.id_fields: dict[str, str] = dict(id_fields or {})
        self.version_fields: dict[str, str] = dict(version_fields or {})
        #: subtype -> (base, discriminator, identity): subtypes live in the base's table
        self.families: dict[str, tuple[str, str, str]] = dict(families or {})
        #: view -> (Aggregate or rows function, model)
        self.views: dict[str, tuple[Any, Any]] = dict(views or {})
        self.tables: dict[str, dict[Any, Any]] = {
            name: {} for name in self.id_fields if name not in self.families | self.views
        }
        self._counters: dict[str, itertools.count[int]] = {}

    @classmethod
    def from_registry(
        cls, registry: ResourceRegistry, *, views: dict[str, Any] | None = None
    ) -> InMemoryStore:
        """A store for every registered resource. ``views`` computes custom
        views (``view = True``): ``{"name": lambda read: rows}``, where
        ``read("invoices")`` returns that resource's records and each row is a
        dict or an entity. Aggregate views need nothing."""
        custom = dict(views or {})
        declared: dict[str, tuple[Any, Any]] = {}
        for d in registry:
            if d.view is None:
                continue
            if d.view is True and d.name not in custom:
                raise ValueError(
                    f"{d.name} is a custom view: pass views={{{d.name!r}: lambda read: [...]}}"
                )
            declared[d.name] = (custom.get(d.name, d.view), d.model)
        return cls(
            {d.name: d.id_field for d in registry},
            {d.name: d.version_field for d in registry if d.version_field},
            {
                d.name: (d.parent.name, d.parent.discriminator or "", d.identity or d.name)
                for d in registry
                if d.parent is not None
            },
            declared,
        )

    def next_id(self, resource: str) -> int:
        counter = self._counters.setdefault(resource, itertools.count(1))
        return next(counter)

    def unit_of_work(self) -> InMemoryUnitOfWork:
        """Use as ``uow_factory=store.unit_of_work``."""
        return InMemoryUnitOfWork(self)

    def outbox_records(self, *, published: bool | None = None) -> list[dict[str, Any]]:
        rows = self.tables.get(OUTBOX_TABLE, {}).values()
        return [copy.deepcopy(r) for r in rows if published is None or r["published"] == published]

    def all(self, resource: str) -> list[Any]:
        family = self.families.get(resource)
        rows = self.tables.get(family[0] if family else resource, {}).values()
        return [copy.deepcopy(e) for e in rows if _of_kind(e, family)]


def _of_kind(entity: Any, family: tuple[str, str, str] | None) -> bool:
    return family is None or getattr(entity, family[1], None) == family[2]


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryRepository[E]:
    supports_conditions = True
    supports_versions = True

    def __init__(
        self,
        store: InMemoryStore,
        table: dict[Any, E],
        resource: str,
        checks: list[tuple[str, Any, int]] | None = None,
    ) -> None:
        self._store = store
        self._table = table
        self._resource = resource
        self._id_field = store.id_fields.get(resource, "id")
        self._version_field = store.version_fields.get(resource, "version")
        self._checks = checks if checks is not None else []
        self._family = store.families.get(resource)
        if self._family is not None:  # a subtype: ids, versions and rows are the base's
            self._resource = self._family[0]

    def _compare(self, entity_id: Any, expected: int) -> None:
        """Against the committed store; re-checked by the unit of work on commit."""
        _check_committed(self._store, self._resource, entity_id, expected, self._version_field)
        self._checks.append((self._resource, entity_id, expected))

    async def get(self, entity_id: Any) -> E | None:
        entity = self._table.get(entity_id)
        return entity if entity is not None and _of_kind(entity, self._family) else None

    async def list(self, query: ListQuery) -> Page[E]:
        from resource_engine.authz.conditions import matches

        items = [
            e
            for e in self._table.values()
            if _of_kind(e, self._family)
            and all(getattr(e, k, None) == v for k, v in query.filters.items())
            and matches(query.condition, e)
        ]
        for key in reversed(list(query.order_by)):
            desc = key.startswith("-")
            name = key.lstrip("-")
            items.sort(
                key=lambda e: (getattr(e, name, None) is None, getattr(e, name, None)), reverse=desc
            )
        total = len(items)
        return Page(
            items[query.offset : query.offset + query.limit], total, query.offset, query.limit
        )

    async def add(self, entity: E) -> E:
        if getattr(entity, self._id_field, None) is None:
            setattr(entity, self._id_field, self._store.next_id(self._resource))
        self._table[getattr(entity, self._id_field)] = entity
        return entity

    async def update(self, entity: E, *, expected_version: int | None = None) -> E:
        entity_id = getattr(entity, self._id_field)
        if expected_version is not None:
            self._compare(entity_id, expected_version)
            setattr(entity, self._version_field, expected_version + 1)
        self._table[entity_id] = entity
        return entity

    async def delete(self, entity: E, *, expected_version: int | None = None) -> None:
        entity_id = getattr(entity, self._id_field)
        if expected_version is not None:
            self._compare(entity_id, expected_version)
        self._table.pop(entity_id, None)


def _check_committed(
    store: InMemoryStore, resource: str, entity_id: Any, expected: int, version_field: str
) -> None:
    stored = store.tables.get(resource, {}).get(entity_id)
    current = getattr(stored, version_field, None) if stored is not None else None
    if current != expected:
        from resource_engine.errors import VersionConflict

        raise VersionConflict(
            f"{resource} {entity_id!r} changed: version {current}, expected {expected}",
            hint="Reload the record and apply your change again.",
            details={
                "resource": resource,
                "id": entity_id,
                "expected": expected,
                "current": current,
                "reason": "version conflict",
            },
        )


class InMemoryOutbox:
    def __init__(self, table: dict[str, dict[str, Any]]) -> None:
        self._table = table

    async def add(self, envelopes: Sequence[EventEnvelope]) -> None:
        for env in envelopes:
            self._table[env.id] = {
                "envelope": env,
                "attempts": 0,
                "last_error": None,
                "published": False,
                "published_at": None,
            }

    async def pending(self, limit: int) -> list[OutboxRecord]:
        rows = [r for r in self._table.values() if not r["published"]][:limit]
        return [OutboxRecord(r["envelope"], r["attempts"], r["last_error"]) for r in rows]

    async def mark_published(self, ids: Sequence[str]) -> None:
        now = datetime.now(UTC)
        for i in ids:
            self._table[i]["published"] = True
            self._table[i]["published_at"] = now

    async def mark_failed(self, ids: Sequence[str], error: str) -> None:
        for i in ids:
            self._table[i]["attempts"] += 1
            self._table[i]["last_error"] = error

    async def purge_published(self, before: datetime, limit: int) -> int:
        old = [
            i
            for i, r in self._table.items()
            if r["published"] and r["published_at"] is not None and r["published_at"] < before
        ][:limit]
        for i in old:
            del self._table[i]
        return len(old)


class InMemoryInbox:
    def __init__(self, table: dict[tuple[str, str], datetime]) -> None:
        self._table = table

    async def processed(self, consumer: str, message_ids: Sequence[str]) -> set[str]:
        return {i for i in message_ids if (consumer, i) in self._table}

    async def mark_processed(self, consumer: str, message_ids: Sequence[str]) -> None:
        now = datetime.now(UTC)
        for i in message_ids:
            self._table.setdefault((consumer, i), now)

    async def purge(self, before: datetime, limit: int) -> int:
        old = [key for key, at in self._table.items() if at < before][:limit]
        for key in old:
            del self._table[key]
        return len(old)


class IdempotencyConflict(RuntimeError):
    """Another unit of work committed the same idempotency key first (what a
    primary key violation is in SQL)."""


class InMemoryIdempotency:
    def __init__(self, table: dict[tuple[str, str], Any], new: set[tuple[str, str]]) -> None:
        self._table = table
        self._new = new

    async def get(self, scope: str, key: str) -> Any:
        record = self._table.get((scope, key))
        return copy.deepcopy(record) if record is not None else None

    async def put(self, record: Any) -> None:
        self._table[(record.scope, record.key)] = copy.deepcopy(record)
        self._new.add((record.scope, record.key))

    async def purge(self, before: datetime, limit: int) -> int:
        old = [k for k, r in self._table.items() if r.expires_at and r.expires_at < before][:limit]
        for k in old:
            del self._table[k]
        return len(old)


class InMemoryViewRepository:
    """A view's rows, computed from the unit of work's tables on each read."""

    supports_conditions = True
    supports_versions = False

    def __init__(self, store: InMemoryStore, working: dict[str, Any], resource: str) -> None:
        self._store = store
        self._working = working
        self._resource = resource

    def _read(self, resource: str) -> list[Any]:
        family = self._store.families.get(resource)
        rows = self._working.get(family[0] if family else resource, {}).values()
        return [e for e in rows if _of_kind(e, family)]

    def _rows(self) -> InMemoryRepository[Any]:
        from resource_engine.application.views import Aggregate, compute

        spec, model = self._store.views[self._resource]
        id_field = self._store.id_fields.get(self._resource, "id")
        raw = (
            compute(spec, self._read(spec.source), id_field)
            if isinstance(spec, Aggregate)
            else spec(self._read)
        )
        entities = [model(**r) if isinstance(r, dict) else r for r in raw]
        table = {getattr(e, id_field): e for e in entities}
        return InMemoryRepository(self._store, table, self._resource)

    async def get(self, entity_id: Any) -> Any:
        return await self._rows().get(entity_id)

    async def list(self, query: ListQuery) -> Page[Any]:
        return await self._rows().list(query)

    async def add(self, entity: Any) -> Any:
        raise TypeError(f"{self._resource} is a view: it cannot be written")

    update = delete = add


class InMemoryUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._working: dict[str, dict[Any, Any]] | None = None
        self._checks: list[tuple[str, Any, int]] = []
        self._new_keys: set[tuple[str, str]] = set()
        self.committed = False

    async def __aenter__(self) -> Self:
        self._working = copy.deepcopy(self._store.tables)
        self._checks = []
        self._new_keys = set()
        self.committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self.committed:
            await self.rollback()

    def repository(self, resource_name: str) -> Any:
        if self._working is None:
            raise RuntimeError("Unit of work is not active")
        if resource_name in self._store.views:
            return InMemoryViewRepository(self._store, self._working, resource_name)
        family = self._store.families.get(resource_name)
        table = self._working.setdefault(family[0] if family else resource_name, {})
        return InMemoryRepository(self._store, table, resource_name, self._checks)

    def outbox(self) -> InMemoryOutbox:
        if self._working is None:
            raise RuntimeError("Unit of work is not active")
        return InMemoryOutbox(self._working.setdefault(OUTBOX_TABLE, {}))

    def inbox(self) -> InMemoryInbox:
        if self._working is None:
            raise RuntimeError("Unit of work is not active")
        return InMemoryInbox(self._working.setdefault(INBOX_TABLE, {}))

    def idempotency(self) -> InMemoryIdempotency:
        if self._working is None:
            raise RuntimeError("Unit of work is not active")
        return InMemoryIdempotency(self._working.setdefault(IDEMPOTENCY_TABLE, {}), self._new_keys)

    async def commit(self) -> None:
        if self._working is None:
            raise RuntimeError("Unit of work is not active")
        committed_keys = self._store.tables.get(IDEMPOTENCY_TABLE, {})
        for key in self._new_keys:  # another unit of work stored the same key first
            existing = committed_keys.get(key)
            if existing is not None and existing.expires_at and existing.expires_at > _now():
                raise IdempotencyConflict(f"Idempotency key {key[1]!r} already committed")
        self._new_keys = set()
        for resource, entity_id, expected in self._checks:  # another unit of work won
            field_name = self._store.version_fields.get(resource, "version")
            _check_committed(self._store, resource, entity_id, expected, field_name)
        self._checks = []
        self._store.tables = self._working
        self._working = copy.deepcopy(self._working)
        self.committed = True

    async def rollback(self) -> None:
        self._checks = []
        self._working = copy.deepcopy(self._store.tables)


class RecordingMessagePublisher:
    """In-memory ``MessagePublisher``. ``fail_ids`` simulates partial failures."""

    def __init__(self) -> None:
        self.messages: list[EventEnvelope] = []
        self.fail_ids: set[str] = set()

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        from resource_engine.errors import PublishFailed

        failed = [m.id for m in messages if m.id in self.fail_ids]
        self.messages.extend(m for m in messages if m.id not in self.fail_ids)
        if failed:
            raise PublishFailed(f"{len(failed)} messages rejected", failed_ids=failed)


class RecordingEventPublisher:
    """Keeps published events in ``self.events``; handy for assertions."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, events: Sequence[DomainEvent]) -> None:
        self.events.extend(events)

    def names(self) -> list[str]:
        return [e.name for e in self.events]


@dataclass
class _Queued:
    envelope: EventEnvelope
    deliveries: int = 0
    visible_at: float = 0.0
    in_flight: bool = False


@dataclass
class InMemoryQueue:
    """In-process broker: a ``MessagePublisher`` and a ``MessageSource`` with
    dead-lettering, delivery counts and retry delays. For tests and prototypes.

    ``dead_letters`` keeps ``(envelope, reason)`` for dead-lettered messages.
    """

    messages: list[_Queued] = field(default_factory=list)
    dead_letters: list[tuple[EventEnvelope, str]] = field(default_factory=list)

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        self.messages.extend(_Queued(m) for m in messages)

    def _ready(self) -> list[_Queued]:
        now = time.monotonic()
        return [q for q in self.messages if not q.in_flight and q.visible_at <= now]

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        deadline = time.monotonic() + wait_seconds
        while not (ready := self._ready()) and time.monotonic() < deadline:
            await asyncio.sleep(min(0.01, wait_seconds))
        batch = ready[:max_messages]
        for q in batch:
            q.in_flight = True
            q.deliveries += 1
        return [ReceivedMessage(q.envelope, q, q.deliveries, None, q.envelope.id) for q in batch]

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        for m in messages:
            if m.handle in self.messages:
                self.messages.remove(m.handle)

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        for m in messages:
            m.handle.in_flight = False
            m.handle.visible_at = time.monotonic() + delay_seconds

    async def dead_letter(self, messages: Sequence[ReceivedMessage], reason: str) -> None:
        for m in messages:
            await self.ack([m])
            self.dead_letters.append((m.handle.envelope, reason))

    @property
    def pending(self) -> int:
        return len(self.messages)


class InMemoryLocks:
    """``LockProvider`` for one process (tests, single-worker deployments)."""

    def __init__(self, clock: Any = None) -> None:
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    async def acquire(self, name: str, owner: str, ttl: Any) -> bool:
        now = self._clock()
        held = self._leases.get(name)
        if held is not None and held[0] != owner and held[1] > now:
            return False
        self._leases[name] = (owner, now + ttl)
        return True

    async def release(self, name: str, owner: str) -> None:
        held = self._leases.get(name)
        if held is not None and held[0] == owner:
            del self._leases[name]

    def holder(self, name: str) -> str | None:
        held = self._leases.get(name)
        return held[0] if held is not None and held[1] > self._clock() else None
