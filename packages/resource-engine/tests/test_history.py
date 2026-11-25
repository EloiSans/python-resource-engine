"""A record's state history, read from the audit trail."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.audit import LoggingAuditSink, StateChange
from resource_engine.audit.history import changes_from
from resource_engine.audit.model import AuditEntry
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, system_principal
from resource_engine.errors import EntityNotFound, HistoryNotAvailable, TransitionNotAllowed
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from statemachine import State, StateMachine


@dataclass
class Ticket:
    title: str
    status: str | None = None
    id: int | None = None


class TicketMachine(StateMachine):
    open = State(initial=True)
    doing = State()
    done = State(final=True)
    start = open.to(doing)
    finish = doing.to(done)


class TicketIn(BaseModel):
    title: str


class TicketOut(TicketIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class TicketResource(Resource):
    model = Ticket
    schemas = {"create": TicketIn, "read": TicketOut}
    operations = ["list", "get", "create"]
    state_machine = TicketMachine


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
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "get", "create"]


ALICE = Principal(subject="alice", issuer="idp", roles=frozenset({"clerks"}))
NOBODY = Principal(subject="eve", issuer="idp", roles=frozenset())


def build(*, audit=True, authz=False):
    registry = ResourceRegistry()
    registry.register(TicketResource)
    registry.register(NoteResource)
    registry.register_audit()
    if authz:
        registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    authorizer = (
        PolicyAuthorizer(
            AuthzConfig(role_source=IdpRoleSource({"clerks": "viewer"})), store.unit_of_work
        )
        if authz
        else None
    )
    return ResourceManager(registry, store.unit_of_work, audit=audit, authorizer=authorizer)


async def test_creation_and_transitions_oldest_first():
    manager = build()
    ticket = await manager.create("tickets", {"title": "Fix"}, actor=ALICE)
    await manager.transition("tickets", ticket.id, "start", actor=ALICE)
    with pytest.raises(TransitionNotAllowed):
        await manager.transition("tickets", ticket.id, "start", actor=ALICE)
    await manager.transition("tickets", ticket.id, "finish", actor=system_principal("bot"))
    history = await manager.history("tickets", ticket.id)
    assert all(isinstance(h, StateChange) for h in history)
    assert [(h.operation, h.event, h.from_state, h.to_state) for h in history] == [
        ("create", None, None, "open"),
        ("transition", "start", "open", "doing"),
        ("transition", "finish", "doing", "done"),
    ]
    assert history[0].actor == history[1].actor != history[2].actor
    assert history[2].actor == "system:bot"
    assert all(h.correlation_id for h in history)
    assert history == sorted(history, key=lambda h: h.at)


async def test_other_records_are_not_mixed_in():
    manager = build()
    one = await manager.create("tickets", {"title": "One"})
    two = await manager.create("tickets", {"title": "Two"})
    await manager.transition("tickets", two.id, "start")
    assert len(await manager.history("tickets", one.id)) == 1
    assert len(await manager.history("tickets", two.id)) == 2


async def test_needs_the_record_to_be_readable():
    manager = build(authz=True)
    ticket = await manager.create("tickets", {"title": "Fix"}, actor=system_principal("seed"))
    assert len(await manager.history("tickets", ticket.id, actor=ALICE)) == 1  # a viewer can get
    with pytest.raises(EntityNotFound):
        await manager.history("tickets", ticket.id, actor=NOBODY)
    with pytest.raises(EntityNotFound):
        await manager.history("tickets", 999, actor=ALICE)


async def test_not_available_without_an_audit_resource_or_a_state_machine():
    with pytest.raises(HistoryNotAvailable, match="not stored"):
        await build(audit=LoggingAuditSink()).history("tickets", 1)
    with pytest.raises(HistoryNotAvailable, match="no state machine"):
        await build().history("notes", 1)
    assert build().has_history and not build(audit=None).has_history


def test_entries_without_the_initial_state():
    def entry(operation, action, changes):
        return AuditEntry(
            resource="tickets", operation=operation, action=action, outcome="ok",
            entity_id="1", changes=changes,
        )  # fmt: skip

    changes = changes_from(
        [
            entry("create", "create", {"title": {"to": "Fix"}}),  # before the state was recorded
            entry("update", "update", {"title": {"from": "Fix", "to": "Fixed"}}),
            entry("transition", "transition:start", {"status": {"from": "open", "to": "doing"}}),
            entry("transition", "transition:finish", {"status": {"changed": True}}),  # masked
        ],
        "status",
    )
    assert [(c.event, c.from_state, c.to_state) for c in changes] == [
        (None, None, "open"),
        ("start", "open", "doing"),
        ("finish", None, None),
    ]
