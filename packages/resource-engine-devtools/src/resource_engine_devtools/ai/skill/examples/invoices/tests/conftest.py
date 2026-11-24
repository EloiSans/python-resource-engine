from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from resource_engine import ResourceManager
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingEventPublisher


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def publisher() -> RecordingEventPublisher:
    return RecordingEventPublisher()


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore.from_registry(registry)


@pytest.fixture
def manager(store, publisher, notifier) -> ResourceManager:
    return ResourceManager(
        registry,
        store.unit_of_work,
        event_publisher=publisher,
        services={"notifier": notifier},
        blob_store=InMemoryBlobStore(),
    )
