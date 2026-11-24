# Testing

Test through the `ResourceManager` with the in-memory adapters: the same
lifecycle, permissions and hooks as in production, without a database. Use
`pytest` with `pytest-asyncio` in auto mode.

```python
import pytest
from resource_engine import ResourceManager
from resource_engine.domain import DomainError
from resource_engine.errors import TransitionNotAllowed
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingEventPublisher

from billing.bootstrap import registry


@pytest.fixture
def store():
    return InMemoryStore.from_registry(registry)


@pytest.fixture
def publisher():
    return RecordingEventPublisher()


@pytest.fixture
def manager(store, publisher):
    return ResourceManager(
        registry,
        store.unit_of_work,
        event_publisher=publisher,
        services={"notifier": FakeNotifier()},
        blob_store=InMemoryBlobStore(),
    )


async def test_rule_in_hook_rolls_back(manager, store, publisher):
    with pytest.raises(DomainError):
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"})
    assert store.all("invoices") == []  # nothing saved
    assert publisher.events == []  # nothing published


async def test_transition(manager):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    await manager.transition("invoices", invoice.id, "submit")
    with pytest.raises(TransitionNotAllowed):
        await manager.transition("invoices", invoice.id, "pay")
```

## What to test

- Domain rules: plain unit tests of the entity methods, no engine.
- Each hook's rule through the manager, including the rollback.
- Each transition: allowed and refused (RE-E201).
- Each action: its postconditions and the events it records
  (`publisher.names()`).
- Permissions: with `PolicyAuthorizer(config, store.unit_of_work)` and
  `Principal(subject=..., issuer=..., roles=frozenset({...}))`, both allowed
  and `PermissionDenied`.
- Over HTTP when routes or status codes matter: build the FastAPI app with
  `mount_resources` and use `httpx.AsyncClient(transport=ASGITransport(app))`.
- With SQLite (`sqlite+aiosqlite`) when the mapping or SQL filters matter.

## Helpers in `resource_engine.testing`

| Helper | Use |
|---|---|
| `InMemoryStore` | Units of work, repositories, outbox, inbox (`store.unit_of_work`, `store.all(name)`, `store.outbox_records()`) |
| `RecordingEventPublisher` | Events published after commit (`events`, `names()`) |
| `InMemoryBlobStore` | Document contents |
| `InMemoryQueue` | A broker for consumers (`publish`, `MessageConsumer(queue, processor)`) |
| `InMemoryLocks` | Leases for `JobRunner` jobs with `single=True` |
| `RecordingTracer` | Spans (`set_tracer(tracer)`, `tracer.named("invoices create")`) |
| `ConditionEngine` | A policy engine with rules in the condition language |
| `contracts.*Contract` | Test suites for your own adapters |

Never mock the manager or the lifecycle: if a test needs it, use the real
manager on the in-memory store.
