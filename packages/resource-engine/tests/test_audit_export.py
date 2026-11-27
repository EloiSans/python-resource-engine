"""Shipping the audit trail (outbox → forwarder → exporters, syslog CEF) and
retention per resource and outcome."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import OperationContext, Resource, ResourceManager, ResourceRegistry
from resource_engine.audit import (
    AuditEntry,
    AuditForwarder,
    AuditJanitor,
    AuditSealer,
    CompositeAuditSink,
    OutboxAuditSink,
    RepositoryAuditSink,
    Retention,
    SyslogAuditExporter,
)
from resource_engine.audit.export import cef, entry_from_dict, entry_to_dict
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer
from resource_engine.errors import ConfigurationError, PermissionDenied, RemoteCallFailed
from resource_engine.messaging import MessageProcessor, OutboxDelivery
from resource_engine.ports.identity import Principal
from resource_engine.processes import ProcessFeed
from resource_engine.testing import InMemoryStore


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


def refuse_boom(ctx: OperationContext) -> None:
    if ctx.data and ctx.data.text == "boom":
        raise ValueError("rolled back")


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "update": NoteIn, "read": NoteOut}
    operations = ["get", "create", "update"]
    hooks = {"after_create": [refuse_boom]}


class Recorder:
    def __init__(self, failures: int = 0) -> None:
        self.entries: list[AuditEntry] = []
        self.failures = failures

    async def export(self, entries):
        if self.failures:
            self.failures -= 1
            raise RemoteCallFailed("SIEM down", service="siem", operation="export")
        self.entries.extend(entries)


def world(*exporters, authz: bool = False):
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_audit(seals=True)
    if authz:
        registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    authorizer = (
        PolicyAuthorizer(AuthzConfig(role_source=IdpRoleSource({})), store.unit_of_work)
        if authz
        else None
    )
    sink = CompositeAuditSink(
        RepositoryAuditSink(store.unit_of_work), OutboxAuditSink(store.unit_of_work)
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        audit=sink,
        event_delivery=OutboxDelivery(),
        authorizer=authorizer,
    )
    processor = MessageProcessor(
        AuditForwarder(*exporters).router(), name="audit-export", manager=manager
    )
    return store, manager, ProcessFeed(store.unit_of_work, processor)


async def test_committed_entries_are_shipped_once_and_rolled_back_ones_never():
    siem = Recorder(failures=1)
    store, manager, feed = world(siem)
    note = await manager.create("notes", {"text": "hello"})
    with pytest.raises(ValueError):
        await manager.create("notes", {"text": "boom"})
    await feed.run_once()
    assert siem.entries == []  # the SIEM was down: retried
    await feed.run_once()
    [entry] = siem.entries
    assert (entry.resource, entry.action, entry.outcome) == ("notes", "create", "ok")
    assert entry.entity_id == str(note.id)
    assert entry.id in {e.id for e in store.tables["audit_log"].values()}  # the same entry
    await feed.run_once()
    assert len(siem.entries) == 1


async def test_refused_attempts_are_shipped_too():
    siem = Recorder()
    store, manager, feed = world(siem, authz=True)
    stranger = Principal(subject="s", issuer="i", roles=frozenset({"nobody"}))
    with pytest.raises(PermissionDenied):
        await manager.create("notes", {"text": "x"}, actor=stranger)
    await feed.run_once()
    [denied] = siem.entries
    assert denied.outcome == "denied" and denied.actor == stranger.id


def test_entries_round_trip_as_json():
    entry = AuditEntry(
        resource="notes",
        operation="update",
        action="update",
        outcome="ok",
        actor="oidc:u",
        changes={"text": {"from": "a", "to": "b"}},
    )
    again = entry_from_dict(entry_to_dict(entry))
    assert again == entry


def test_cef_escapes_and_carries_the_fields():
    entry = AuditEntry(
        resource="notes",
        operation="update",
        action="update",
        outcome="denied",
        actor="oidc:a|b",
        tenant="acme",
        changes={"text": {"from": "x=1", "to": "y"}},
    )
    line = cef(entry, vendor="Resource Engine", product="app", version="1")
    assert line.startswith("CEF:0|Resource Engine|app|1|notes:update|notes update denied|6|")
    assert "suser=oidc:a|b" in line and "cs3=acme" in line and f"externalId={entry.id}" in line
    assert '\\"x\\=1\\"' in line or "x\\=1" in line  # '=' escaped in extensions


async def _syslog_server(kind: str):
    received: list[bytes] = []
    loop = asyncio.get_running_loop()
    if kind == "udp":

        class Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                received.append(data)

        transport, _ = await loop.create_datagram_endpoint(Protocol, local_addr=("127.0.0.1", 0))
        return transport.get_extra_info("sockname")[1], received, transport.close

    async def handle(reader, writer):
        received.append(await reader.read(65536))
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server.sockets[0].getsockname()[1], received, server.close


@pytest.mark.parametrize("kind", ["udp", "tcp"])
async def test_syslog_rfc5424_messages(kind):
    port, received, close = await _syslog_server(kind)
    try:
        exporter = SyslogAuditExporter(
            "127.0.0.1", port, transport=kind, app_name="invoices", hostname="host1"
        )
        entry = AuditEntry(resource="notes", operation="create", action="create", outcome="ok")
        await exporter.export([entry])
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)
    finally:
        close()
    message = received[0].decode()
    if kind == "tcp":
        length, _, message = message.partition(" ")
        assert int(length) == len(message.encode())
    assert message.startswith("<110>1 ")  # facility 13 (log audit), informational
    assert " host1 invoices - audit - CEF:0|" in message


async def test_an_unreachable_syslog_collector_is_retried():
    exporter = SyslogAuditExporter("127.0.0.1", 9, transport="tcp", timeout=2)
    entry = AuditEntry(resource="notes", operation="create", action="create", outcome="ok")
    with pytest.raises(RemoteCallFailed):
        await exporter.export([entry])


# --------------------------------------------------------------- retention


def _entry(store, resource: str, outcome: str, days_old: int, seal: int | None = None):
    entry = AuditEntry(
        resource=resource,
        operation="create",
        action="create",
        outcome=outcome,
        at=datetime.now(UTC) - timedelta(days=days_old),
        seal=seal,
    )
    store.tables["audit_log"][entry.id] = entry
    return entry


@pytest.fixture
def store():
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register_audit(seals=True)
    return InMemoryStore.from_registry(registry)


async def test_rules_per_resource_and_outcome_before_the_default(store):
    keep = {
        _entry(store, "invoices", "ok", 400).id,  # invoices: kept forever
        _entry(store, "notes", "ok", 20).id,  # default: 90 days
        _entry(store, "notes", "denied", 5).id,  # denied: 7 days
    }
    gone = {
        _entry(store, "notes", "ok", 100).id,
        _entry(store, "notes", "denied", 10).id,
        _entry(store, "tags", "failed", 10).id,
    }
    janitor = AuditJanitor(
        store.unit_of_work,
        retention=timedelta(days=90),
        rules=[
            Retention(None, resource="invoices"),
            Retention(timedelta(days=7), outcome=["denied", "failed"]),
        ],
    )
    stats = await janitor.run_once()
    assert stats.purged == 3
    assert set(store.tables["audit_log"]) == keep and not gone & set(store.tables["audit_log"])


async def test_sealed_batches_go_when_every_entry_is_past_its_rule(store):
    sealer = AuditSealer(store.unit_of_work, batch_size=2)
    _entry(store, "notes", "denied", 40)
    _entry(store, "notes", "denied", 40)
    _entry(store, "notes", "denied", 40)
    _entry(store, "invoices", "ok", 40)  # sealed with a denied one, kept for a year
    janitor = AuditJanitor(
        store.unit_of_work,
        sealer=sealer,
        rules=[
            Retention(timedelta(days=30), outcome="denied"),
            Retention(timedelta(days=365), resource="invoices"),
        ],
    )
    stats = await janitor.run_once()
    assert stats.seals == 2 and stats.purged_seals == 1 and stats.purged == 2
    left = store.tables["audit_log"].values()
    assert {e.resource for e in left} == {"notes", "invoices"} and len(left) == 2
    report = await sealer.verify()
    assert report.ok and report.purged_seals == 1


def test_rule_settings():
    with pytest.raises(ConfigurationError):
        Retention(timedelta(days=1))  # neither a resource nor an outcome
    with pytest.raises(ConfigurationError):
        Retention(timedelta(0), resource="notes")
    janitor = AuditJanitor(
        InMemoryStore().unit_of_work, rules=[Retention(timedelta(days=1), resource="x")]
    )
    assert janitor.retention is None
