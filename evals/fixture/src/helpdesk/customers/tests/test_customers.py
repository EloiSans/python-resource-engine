"""Lifecycle tests of the customers module against the in-memory adapters."""

from __future__ import annotations

import pytest
from resource_engine import ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore, RecordingEventPublisher

from helpdesk.customers.application.resource import CustomerResource

pytestmark = pytest.mark.asyncio


@pytest.fixture
def publisher():
    return RecordingEventPublisher()


@pytest.fixture
def manager(publisher):
    registry = ResourceRegistry()
    registry.register(CustomerResource)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work, event_publisher=publisher)


async def test_create_get_update_delete(manager, publisher):
    created = await manager.create("customers", {"name": "Sample"})
    assert (await manager.get("customers", created.id)).id == created.id
    updated = await manager.update("customers", created.id, {"name": "Changed"})
    assert updated.name != created.name
    await manager.delete("customers", created.id)
    assert (await manager.list("customers")).total == 0
    assert publisher.names() == ["ResourceCreated", "ResourceUpdated", "ResourceDeleted"]
