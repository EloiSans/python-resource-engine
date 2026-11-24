"""Hidden checks for the tickets task (copied in after the agent finishes)."""

from __future__ import annotations

import pytest
from helpdesk.authz import seed
from helpdesk.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import PermissionDenied, TransitionNotAllowed
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore

pytestmark = pytest.mark.asyncio

ADMIN = Principal(subject="root", issuer="idp", roles=frozenset({"helpdesk-admin"}))
AGENT = Principal(subject="ana", issuer="idp", roles=frozenset({"agent"}))
SUPERVISOR = Principal(subject="sam", issuer="idp", roles=frozenset({"supervisor"}))


@pytest.fixture
async def manager():
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=PolicyAuthorizer(authz, store.unit_of_work)
    )
    await seed(manager, ADMIN)
    return manager


def test_declaration():
    tickets = registry.get("tickets")
    assert set(tickets.operations) == {"list", "get", "create", "update"}
    assert set(tickets.transitions) == {"start", "resolve", "cancel"}
    assert "status" not in tickets.schemas["create"].model_fields
    assert "status" not in tickets.schemas["update"].model_fields
    assert "status" in tickets.schemas["read"].model_fields


async def test_agent_works_a_ticket(manager):
    ticket = await manager.create("tickets", {"customer_id": 1, "subject": "Printer"}, actor=AGENT)
    assert ticket.status == "open"
    await manager.update("tickets", ticket.id, {"description": "Out of toner"}, actor=AGENT)
    ticket = await manager.transition("tickets", ticket.id, "start", actor=AGENT)
    assert ticket.status == "in_progress"
    ticket = await manager.transition("tickets", ticket.id, "resolve", actor=AGENT)
    assert ticket.status == "resolved"


async def test_resolve_needs_start(manager):
    ticket = await manager.create("tickets", {"customer_id": 1, "subject": "Login"}, actor=AGENT)
    with pytest.raises(TransitionNotAllowed):
        await manager.transition("tickets", ticket.id, "resolve", actor=AGENT)


async def test_only_supervisors_cancel(manager):
    ticket = await manager.create("tickets", {"customer_id": 1, "subject": "Phone"}, actor=AGENT)
    with pytest.raises(PermissionDenied):
        await manager.transition("tickets", ticket.id, "cancel", actor=AGENT)
    ticket = await manager.transition("tickets", ticket.id, "cancel", actor=SUPERVISOR)
    assert ticket.status == "cancelled"
    with pytest.raises(PermissionDenied):
        await manager.create("tickets", {"customer_id": 1, "subject": "X"}, actor=SUPERVISOR)


async def test_subject_is_required(manager):
    from resource_engine.domain import DomainError
    from resource_engine.errors import ValidationFailed

    with pytest.raises((DomainError, ValidationFailed)):
        await manager.create("tickets", {"customer_id": 1, "subject": "  "}, actor=AGENT)
