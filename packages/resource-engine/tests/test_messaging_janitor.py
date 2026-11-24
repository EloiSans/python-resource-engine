"""MessagingJanitor: retention of inbox marks and published outbox records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from resource_engine.errors import ConfigurationError
from resource_engine.messaging import MessagingJanitor
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import contract_envelope as envelope


async def seed(store: InMemoryStore, published: int, pending: int, marks: int) -> None:
    envelopes = [envelope(n) for n in range(published + pending)]
    async with store.unit_of_work() as uow:
        await uow.outbox().add(envelopes)
        await uow.outbox().mark_published([e.id for e in envelopes[:published]])
        await uow.inbox().mark_processed("billing", [f"m{i}" for i in range(marks)])
        await uow.commit()


async def test_purges_past_retention_in_batches():
    store = InMemoryStore()
    await seed(store, published=5, pending=2, marks=7)
    janitor = MessagingJanitor(store.unit_of_work, batch_size=2)
    assert (await janitor.run_once()).inbox == 0  # still within retention
    later = datetime.now(UTC) + timedelta(days=30)
    stats = await janitor.run_once(now=later)
    assert (stats.inbox, stats.outbox) == (7, 5)
    assert len(store.outbox_records(published=False)) == 2
    assert store.outbox_records(published=True) == []


async def test_retention_per_table():
    store = InMemoryStore()
    await seed(store, published=1, pending=0, marks=1)
    later = datetime.now(UTC) + timedelta(days=10)  # past outbox (7), within inbox (14)
    stats = await MessagingJanitor(store.unit_of_work).run_once(now=later)
    assert (stats.inbox, stats.outbox) == (0, 1)
    stats = await MessagingJanitor(store.unit_of_work, inbox_retention=None).run_once(
        now=later + timedelta(days=30)
    )
    assert stats.inbox == 0


async def test_missing_table_is_a_configuration_error():
    class NoTables:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    with pytest.raises(ConfigurationError, match="no inbox"):
        await MessagingJanitor(NoTables).run_once()
    await MessagingJanitor(NoTables, inbox_retention=None, outbox_retention=None).run_once()


@pytest.mark.parametrize(
    "kwargs",
    [{"batch_size": 0}, {"inbox_retention": timedelta(0)}, {"outbox_retention": timedelta(-1)}],
)
def test_invalid_settings(kwargs):
    with pytest.raises(ConfigurationError):
        MessagingJanitor(InMemoryStore().unit_of_work, **kwargs)
