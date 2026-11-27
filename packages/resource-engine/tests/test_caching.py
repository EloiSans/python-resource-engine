"""The record cache: reads by id through the cache, writes forget, permissions
and tenancy still run per caller, transactions and other kinds bypass it."""

from __future__ import annotations

import pytest
from invoices.bootstrap import registry
from resource_engine import ResourceManager
from resource_engine.cache import InMemoryCache, restore, snapshot
from resource_engine.errors import EntityNotFound
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


class _Silent:
    async def send(self, message: str) -> None:
        pass


@pytest.fixture
def setup():
    store = InMemoryStore.from_registry(registry)
    cache = InMemoryCache()
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        cache=cache,
    )
    return manager, store, cache


async def _behind_its_back(store, resource, entity_id, **changes):
    """Change a stored record without the manager (another service, SQL)."""
    for name, value in changes.items():
        setattr(store.tables[resource][entity_id], name, value)


async def test_reads_come_from_the_cache_until_a_write(setup):
    manager, store, cache = setup
    acme = await manager.create("customers", {"name": "ACME", "tax_id": "B111"})
    assert (await manager.get("customers", acme.id)).name == "ACME"  # cached now
    await _behind_its_back(store, "customers", acme.id, name="Changed elsewhere")
    assert (await manager.get("customers", acme.id)).name == "ACME"  # from the cache
    await manager.update("customers", acme.id, {"name": "ACME Corp"})
    assert (await manager.get("customers", acme.id)).name == "ACME Corp"  # forgotten
    await manager.delete("customers", acme.id)
    with pytest.raises(EntityNotFound):
        await manager.get("customers", acme.id)


async def test_only_resources_with_a_ttl_and_never_inside_transactions(setup):
    manager, store, cache = setup
    invoice = await manager.create("invoices", {"customer": "x", "amount": "1"})
    await manager.get("invoices", invoice.id)
    assert not cache._items  # invoices have no cache_ttl
    acme = await manager.create("customers", {"name": "ACME", "tax_id": "B111"})
    async with manager.transaction():
        await manager.get("customers", acme.id)
    assert not cache._items  # transactions read the repository
    await manager.get("customers", acme.id)
    assert len(cache._items) == 1


async def test_a_broken_cache_is_a_miss(setup):
    manager, _, cache = setup
    acme = await manager.create("customers", {"name": "ACME", "tax_id": "B111"})

    async def broken(key):
        raise ConnectionError("cache down")

    cache.get = broken
    assert (await manager.get("customers", acme.id)).name == "ACME"


def test_snapshots_round_trip():
    from decimal import Decimal

    from invoices.domain.entities import Invoice

    invoice = Invoice(customer="x", amount=Decimal("1.50"), id=3, status="draft")
    copy = restore(snapshot(invoice))
    assert type(copy) is Invoice and copy == invoice


async def test_add_stores_only_absent_keys():
    cache = InMemoryCache()
    assert await cache.add("k", 1, ttl=30) is True
    assert await cache.add("k", 2, ttl=30) is False
    assert await cache.get("k") == 1
    await cache.set("gone", 1, ttl=-1)
    assert await cache.add("gone", 2, ttl=30) is True
