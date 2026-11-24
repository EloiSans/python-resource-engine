"""Lifecycle tests of the tickets module against the in-memory adapters."""

from __future__ import annotations

import pytest
from helpdesk.tickets.application.resource import TicketResource
from resource_engine import ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore, RecordingEventPublisher

pytestmark = pytest.mark.asyncio


@pytest.fixture
def publisher():
    return RecordingEventPublisher()


@pytest.fixture
def manager(publisher):
    registry = ResourceRegistry()
    registry.register(TicketResource)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry, store.unit_of_work, event_publisher=publisher, services={"mailer": Mailer()}
    )


class Mailer:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str]] = []

    async def send(self, customer_id: int, subject: str, body: str) -> None:
        self.sent.append((customer_id, subject, body))


async def test_create_get_update(manager, publisher):
    created = await manager.create("tickets", {"customer_id": 1, "subject": "Sample"})
    assert (await manager.get("tickets", created.id)).id == created.id
    updated = await manager.update("tickets", created.id, {"customer_id": 2})
    assert updated.customer_id != created.customer_id
    assert publisher.names() == ["ResourceCreated", "ResourceUpdated"]


async def test_starts_in_open_and_start(manager):
    created = await manager.create("tickets", {"customer_id": 1, "subject": "Sample"})
    assert created.status == "open"
    moved = await manager.transition("tickets", created.id, "start")
    assert moved.status == "in_progress"


async def test_resolve_needs_the_ticket_started(manager):
    from resource_engine.errors import TransitionNotAllowed

    created = await manager.create("tickets", {"customer_id": 1, "subject": "Sample"})
    with pytest.raises(TransitionNotAllowed):
        await manager.transition("tickets", created.id, "resolve")
    await manager.transition("tickets", created.id, "start")
    assert (await manager.transition("tickets", created.id, "resolve")).status == "resolved"


async def test_resolving_emails_the_customer(manager):
    created = await manager.create("tickets", {"customer_id": 7, "subject": "Sample"})
    await manager.transition("tickets", created.id, "start")
    await manager.transition("tickets", created.id, "resolve")
    (sent,) = manager.services["mailer"].sent
    assert sent[0] == 7


async def test_reassign(manager):
    created = await manager.create("tickets", {"customer_id": 1, "subject": "Sample"})
    ticket = await manager.run_action("tickets", "reassign", created.id, {"assignee": "joan"})
    assert ticket.assignee == "joan"
