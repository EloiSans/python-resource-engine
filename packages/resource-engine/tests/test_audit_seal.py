"""Audit trail: sealed batches (tamper evidence) and retention."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.audit import AuditJanitor, AuditSealer
from resource_engine.errors import ConfigurationError
from resource_engine.testing import InMemoryStore

LATER = timedelta(hours=1)


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "update": NoteIn, "read": NoteOut}
    operations = ["get", "create", "update"]


@pytest.fixture
def store():
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_audit(seals=True)
    store = InMemoryStore.from_registry(registry)
    store.manager = ResourceManager(registry, store.unit_of_work, audit=True)
    return store


async def write(store, n: int) -> None:
    for i in range(n):
        await store.manager.create("notes", {"text": f"note {i}"})


def entries(store):
    return store.tables["audit_log"]


def seals(store):
    return store.tables["audit_seals"]


async def test_seals_in_chained_batches_and_verifies(store):
    await write(store, 5)
    sealer = AuditSealer(store.unit_of_work, batch_size=2)
    now = datetime.now(UTC) + LATER
    created = await sealer.seal_pending(now)
    assert [s.entries for s in created] == [2, 2, 1]
    assert [s.previous for s in created] == [None, created[0].hash, created[1].hash]
    assert all(e.seal is not None for e in entries(store).values())
    assert await sealer.seal_pending(now) == []
    report = await sealer.verify()
    assert report.ok and (report.seals, report.entries, report.unsealed) == (3, 5, 0)
    page = await store.manager.list("audit_log", ListQuery(filters={"seal": 1}))
    assert page.total == 2


async def test_recent_entries_wait_for_the_next_run(store):
    await write(store, 2)
    sealer = AuditSealer(store.unit_of_work)
    assert await sealer.seal_pending() == []  # younger than settle
    report = await sealer.verify()
    assert report.ok and report.unsealed == 2


@pytest.mark.parametrize(
    ("tamper", "kind"),
    [
        (lambda s: setattr(next(iter(entries(s).values())), "actor", "mallory"), "entries_changed"),
        (lambda s: entries(s).pop(next(iter(entries(s)))), "entries_changed"),
        (lambda s: setattr(seals(s)[1], "digest", "0" * 64), "seal_changed"),
        (lambda s: seals(s).pop(1), "seal_missing"),
        (lambda s: setattr(seals(s)[2], "previous", "f" * 64), "chain_broken"),
        (lambda s: setattr(next(iter(entries(s).values())), "seal", 9), "orphan_entries"),
    ],
)
async def test_verify_reports_tampering(store, tamper, kind):
    await write(store, 4)
    sealer = AuditSealer(store.unit_of_work, batch_size=2)
    await sealer.seal_pending(datetime.now(UTC) + LATER)
    tamper(store)
    report = await sealer.verify()
    assert not report.ok
    assert kind in {p.kind for p in report.problems}


async def test_hmac_resists_recomputed_seals(store):
    from resource_engine.audit.seal import entries_digest, seal_hash

    await write(store, 2)
    sealer = AuditSealer(store.unit_of_work, key=b"secret", key_id="k1")
    await sealer.seal_pending(datetime.now(UTC) + LATER)
    assert (await sealer.verify()).ok
    # rewrite an entry and recompute digest and hash, without the key
    entry = next(iter(entries(store).values()))
    entry.actor = "mallory"
    seal = seals(store)[1]
    batch = sorted(entries(store).values(), key=lambda e: (e.at, e.id))
    seal.digest = entries_digest(batch)
    seal.hash = seal_hash(1, None, seal.entries, seal.first_at, seal.last_at, seal.digest)
    assert {p.kind for p in (await sealer.verify()).problems} == {"bad_mac"}
    assert (await AuditSealer(store.unit_of_work).verify()).ok  # no key: not detectable


async def test_key_rotation_and_unsigned_seals(store):
    await write(store, 1)
    await AuditSealer(store.unit_of_work, settle=timedelta(0)).seal_pending()
    await write(store, 1)
    old = AuditSealer(store.unit_of_work, key=b"one", key_id="k1", settle=timedelta(0))
    await old.seal_pending()
    await write(store, 1)
    new = AuditSealer(
        store.unit_of_work, key=b"two", key_id="k2", old_keys={"k1": b"one"}, settle=timedelta(0)
    )
    await new.seal_pending()
    kinds = [(p.sequence, p.kind) for p in (await new.verify()).problems]
    assert kinds == [(1, "unsigned")]
    no_old = AuditSealer(store.unit_of_work, key=b"two", key_id="k2")
    assert (2, "unknown_key") in [(p.sequence, p.kind) for p in (await no_old.verify()).problems]


async def test_retention_without_sealer(store):
    await write(store, 3)
    janitor = AuditJanitor(store.unit_of_work, retention=timedelta(days=30), batch_size=2)
    assert (await janitor.run_once()).purged == 0
    stats = await janitor.run_once(datetime.now(UTC) + timedelta(days=31))
    assert stats.purged == 3 and entries(store) == {}


async def test_retention_keeps_seals_and_unsealed_entries(store):
    await write(store, 3)
    sealer = AuditSealer(store.unit_of_work, batch_size=2)
    janitor = AuditJanitor(store.unit_of_work, retention=timedelta(days=30), sealer=sealer)
    stats = await janitor.run_once(datetime.now(UTC) + LATER)
    assert (stats.sealed, stats.seals, stats.purged) == (3, 2, 0)
    await write(store, 1)  # recent, stays unsealed below
    stats = await AuditJanitor(
        store.unit_of_work,
        retention=timedelta(days=30),
        sealer=AuditSealer(store.unit_of_work, settle=timedelta(days=365)),
    ).run_once(datetime.now(UTC) + timedelta(days=31))
    assert (stats.purged, stats.purged_seals) == (3, 2)
    assert len(entries(store)) == 1 and len(seals(store)) == 2
    assert all(s.purged_at is not None for s in seals(store).values())
    report = await sealer.verify()
    assert report.ok and report.purged_seals == 2 and report.unsealed == 1


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"retention": timedelta(0)}, {"retention": timedelta(days=1), "batch_size": 0}],
)
def test_janitor_settings(kwargs):
    with pytest.raises(ConfigurationError):
        AuditJanitor(InMemoryStore().unit_of_work, **kwargs)


@pytest.mark.parametrize("kwargs", [{"batch_size": 0}, {"settle": timedelta(-1)}, {"key": b""}])
def test_sealer_settings(kwargs):
    with pytest.raises(ConfigurationError):
        AuditSealer(InMemoryStore().unit_of_work, **kwargs)
