"""Tamper evidence for the audit trail: sealed batches, chained.

Entries are written by concurrent transactions, so they are not chained one by
one. ``AuditSealer.seal_pending()`` (a periodic job) takes the unsealed entries
older than ``settle``, in ``(at, id)`` order, and seals them in batches:

- ``digest``: SHA-256 over the batch's entries in canonical JSON;
- ``hash``: SHA-256 over the digest, the batch's metadata and the previous
  seal's hash, so each seal commits to the whole chain before it;
- ``mac``: HMAC-SHA-256 of ``hash`` when a key is configured, so whoever can
  write the database cannot recompute the seals without the key.

``verify()`` walks the chain and reports entries changed, deleted or moved
between seals, seals changed, missing or reordered, and bad HMACs. Entries
written after the last run are not covered until the next one. Entries added
by someone with write access are sealed like any other: sealing detects later
changes, not forged writes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from resource_engine.application.query import ListQuery
from resource_engine.audit.model import AuditEntry, AuditSeal
from resource_engine.audit.recorder import jsonable
from resource_engine.errors import ConfigurationError

UNSEALED = {"eq": [{"attr": "resource.seal"}, None]}


def _utc(value: datetime) -> datetime:
    """SQLite gives naive datetimes back: they are UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def canonical(entry: AuditEntry) -> bytes:
    """The bytes an entry is sealed as: every field but ``seal``, as sorted,
    compact JSON, with ``at`` in UTC."""
    data = {f.name: getattr(entry, f.name) for f in dataclasses.fields(AuditEntry)}
    data.pop("seal")
    data["at"] = _utc(data["at"]).isoformat()
    return json.dumps(
        jsonable(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def entries_digest(entries: list[AuditEntry]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(canonical(entry))
        digest.update(b"\n")
    return digest.hexdigest()


def seal_hash(
    sequence: int,
    previous: str | None,
    entries: int,
    first_at: datetime,
    last_at: datetime,
    digest: str,
) -> str:
    material = {
        "sequence": sequence,
        "previous": previous,
        "entries": entries,
        "first_at": _utc(first_at).isoformat(),
        "last_at": _utc(last_at).isoformat(),
        "digest": digest,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _mac(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AuditProblem:
    """One finding of ``AuditSealer.verify``.

    ``kind``: ``entries_changed`` (digest or count differs: entries changed,
    deleted or moved), ``seal_changed`` (the seal does not hash to itself),
    ``chain_broken`` (``previous`` does not match), ``seal_missing`` (a gap in
    the sequence), ``bad_mac``, ``unsigned`` (a key is configured but the seal
    has no HMAC), ``unknown_key``, ``orphan_entries`` (entries pointing at a
    seal that does not exist).
    """

    sequence: int | None
    kind: str
    message: str


@dataclass
class AuditVerification:
    seals: int = 0
    entries: int = 0
    purged_seals: int = 0
    unsealed: int = 0
    problems: list[AuditProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class AuditSealer:
    """Seals the audit trail in batches and verifies the chain.

    Args:
        uow_factory: Units of work with the ``audit_log`` and ``audit_seals``
            repositories (``registry.register_audit(seals=True)``).
        key: HMAC key. Without it, seals are plain hashes: they show accidental
            or partial edits, not a rewrite of entries and seals together.
        key_id: Stored with each seal, to rotate keys.
        old_keys: Earlier keys by id, to verify seals they produced.
        settle: Only entries older than this are sealed, so transactions still
            running when the sealer starts get into a later batch, not a gap.
        batch_size: Entries per seal (one transaction each).

    Run one sealer at a time. In SQL, two sealers racing collide on the seal's
    primary key and one of them rolls back.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        key: bytes | None = None,
        key_id: str = "default",
        old_keys: Mapping[str, bytes] | None = None,
        settle: timedelta = timedelta(minutes=5),
        batch_size: int = 1000,
        entries: str = "audit_log",
        seals: str = "audit_seals",
    ) -> None:
        if batch_size < 1:
            raise ConfigurationError("batch_size must be at least 1")
        if settle < timedelta(0):
            raise ConfigurationError("settle cannot be negative")
        if key is not None and not key:
            raise ConfigurationError("The HMAC key is empty", hint="Pass key=None for no HMAC.")
        self.uow_factory = uow_factory
        self.key = key
        self.key_id = key_id
        self.keys: dict[str, bytes] = dict(old_keys or {})
        if key is not None:
            self.keys[key_id] = key
        self.settle = settle
        self.batch_size = batch_size
        self.entries_resource = entries
        self.seals_resource = seals

    # ------------------------------------------------------------------ seal

    async def seal_pending(self, now: datetime | None = None) -> list[AuditSeal]:
        """Seal the unsealed entries older than ``settle``. Returns the new seals."""
        cutoff = (now or datetime.now(UTC)) - self.settle
        sealed: list[AuditSeal] = []
        while True:
            async with self.uow_factory() as uow:
                entries_repo = uow.repository(self.entries_resource)
                seals_repo = uow.repository(self.seals_resource)
                page = await entries_repo.list(
                    ListQuery(
                        limit=self.batch_size,
                        condition={"and": [UNSEALED, _before(cutoff)]},
                        order_by=["at", "id"],
                    )
                )
                batch = page.items
                if not batch:
                    return sealed
                last = await _last_seal(seals_repo)
                sequence = (last.sequence if last else 0) + 1
                seal = self._seal(sequence, last.hash if last else None, batch)
                for entry in batch:
                    entry.seal = sequence
                    await entries_repo.update(entry)
                await seals_repo.add(seal)
                sealed.append(dataclasses.replace(seal))  # usable after the session ends
                await uow.commit()
            if len(batch) < self.batch_size:
                return sealed

    def _seal(self, sequence: int, previous: str | None, batch: list[AuditEntry]) -> AuditSeal:
        digest = entries_digest(batch)
        first_at = min(_utc(e.at) for e in batch)
        last_at = max(_utc(e.at) for e in batch)
        value = seal_hash(sequence, previous, len(batch), first_at, last_at, digest)
        return AuditSeal(
            sequence=sequence,
            previous=previous,
            entries=len(batch),
            first_at=first_at,
            last_at=last_at,
            digest=digest,
            hash=value,
            mac=_mac(self.key, value) if self.key is not None else None,
            key_id=self.key_id if self.key is not None else None,
        )

    # ---------------------------------------------------------------- verify

    async def verify(self) -> AuditVerification:
        """Walk the whole chain and recompute every seal and batch."""
        report = AuditVerification()
        previous: AuditSeal | None = None
        offset = 0
        async with self.uow_factory() as uow:
            entries_repo = uow.repository(self.entries_resource)
            seals_repo = uow.repository(self.seals_resource)
            while True:
                page = await seals_repo.list(
                    ListQuery(offset=offset, limit=500, order_by=["sequence"])
                )
                for seal in page.items:
                    await self._check(seal, previous, entries_repo, report)
                    previous = seal
                offset += len(page.items)
                if len(page.items) < 500:
                    break
            last = previous.sequence if previous else 0
            orphans = await entries_repo.list(
                ListQuery(limit=1, condition={"gt": [{"attr": "resource.seal"}, last]})
            )
            if orphans.total:
                report.problems.append(
                    AuditProblem(
                        None,
                        "orphan_entries",
                        f"{orphans.total} entries point at seals after {last}",
                    )
                )
            unsealed = await entries_repo.list(ListQuery(limit=1, condition=UNSEALED))
            report.unsealed = unsealed.total
        return report

    async def _check(
        self,
        seal: AuditSeal,
        previous: AuditSeal | None,
        entries_repo: Any,
        report: AuditVerification,
    ) -> None:
        report.seals += 1
        problems = report.problems
        seq = seal.sequence
        expected_seq = (previous.sequence if previous else 0) + 1
        if seq != expected_seq:
            problems.append(
                AuditProblem(seq, "seal_missing", f"seals {expected_seq}..{seq - 1} are missing")
            )
        if seal.previous != (previous.hash if previous else None):
            problems.append(
                AuditProblem(seq, "chain_broken", "previous does not match the seal before")
            )
        recomputed = seal_hash(
            seq, seal.previous, seal.entries, seal.first_at, seal.last_at, seal.digest
        )
        if recomputed != seal.hash:
            problems.append(AuditProblem(seq, "seal_changed", "the seal does not hash to itself"))
        if self.keys:
            if seal.mac is None:
                problems.append(AuditProblem(seq, "unsigned", "the seal has no HMAC"))
            elif seal.key_id not in self.keys:
                problems.append(
                    AuditProblem(seq, "unknown_key", f"no key {seal.key_id!r} to check the HMAC")
                )
            elif not hmac.compare_digest(_mac(self.keys[seal.key_id], seal.hash), seal.mac):
                problems.append(AuditProblem(seq, "bad_mac", "the HMAC does not match"))
        if seal.purged_at is not None:
            report.purged_seals += 1
            return
        batch = await _batch(entries_repo, seq, max(seal.entries, 1) + 1)
        report.entries += len(batch)
        if len(batch) != seal.entries or entries_digest(batch) != seal.digest:
            problems.append(
                AuditProblem(
                    seq,
                    "entries_changed",
                    f"{len(batch)} entries found, {seal.entries} sealed; or their content changed",
                )
            )


def _before(cutoff: datetime) -> dict[str, Any]:
    return {"lt": [{"attr": "resource.at"}, cutoff]}


async def _last_seal(seals_repo: Any) -> AuditSeal | None:
    page = await seals_repo.list(ListQuery(limit=1, order_by=["-sequence"]))
    return page.items[0] if page.items else None


async def _batch(entries_repo: Any, sequence: int, limit: int) -> list[AuditEntry]:
    """The entries of a seal, in sealing order. ``limit`` is one more than the
    seal's count, to notice entries added to the batch."""
    items: list[AuditEntry] = []
    offset = 0
    while True:
        page = await entries_repo.list(
            ListQuery(
                offset=offset,
                limit=min(limit - len(items), 1000),
                filters={"seal": sequence},
                order_by=["at", "id"],
            )
        )
        items += page.items
        offset += len(page.items)
        if not page.items or len(items) >= limit:
            return items
