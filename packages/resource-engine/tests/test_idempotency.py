"""Idempotency keys: a retried write returns the first result instead of
running again; a reused key with another request is refused."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from invoices.authz import seed
from invoices.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.application.idempotency import IdempotencyJanitor
from resource_engine.authz import PolicyAuthorizer
from resource_engine.domain import DomainError
from resource_engine.errors import (
    IdempotencyKeyReused,
    IdempotencyNotSupported,
    ValidationFailed,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingEventPublisher
from resource_engine.testing.contracts import IdempotencyContract

ADMIN = Principal(subject="root", issuer="idp", roles=frozenset({"platform-admin"}))
ANA = Principal(subject="ana", issuer="idp", roles=frozenset({"issuer"}))
BOB = Principal(subject="bob", issuer="idp", roles=frozenset({"issuer"}))


@pytest.fixture
def store():
    return InMemoryStore.from_registry(registry)


@pytest.fixture
def publisher():
    return RecordingEventPublisher()


@pytest.fixture
def manager(store, publisher):
    return ResourceManager(
        registry, store.unit_of_work, event_publisher=publisher, blob_store=InMemoryBlobStore()
    )


async def test_retried_create_returns_the_first_result(manager, store, publisher):
    data = {"customer": "ACME", "amount": "10"}
    first = await manager.create("invoices", data, idempotency_key="k-1")
    again = await manager.create("invoices", data, idempotency_key="k-1")
    assert again == first and again.id == first.id
    assert len(store.all("invoices")) == 1
    assert publisher.names() == ["ResourceCreated"]  # the replay publishes nothing
    other = await manager.create("invoices", data, idempotency_key="k-2")
    assert other.id != first.id


async def test_reused_key_with_another_request_is_refused(manager):
    await manager.create("invoices", {"customer": "ACME", "amount": "10"}, idempotency_key="k")
    with pytest.raises(IdempotencyKeyReused) as info:
        await manager.create("invoices", {"customer": "ACME", "amount": "11"}, idempotency_key="k")
    assert info.value.details["first"] == {"resource": "invoices", "operation": "create"}
    with pytest.raises(IdempotencyKeyReused):
        await manager.update("invoices", 1, {"amount": "12"}, idempotency_key="k")


async def test_every_write_replays(manager, publisher):
    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "10"})
    for _ in range(2):
        updated = await manager.update(
            "invoices", invoice.id, {"amount": "12"}, idempotency_key="u"
        )
        submitted = await manager.transition("invoices", invoice.id, "submit", idempotency_key="t")
    assert updated.amount == 12 and submitted.status == "pending"
    other = await manager.create("invoices", {"customer": "B", "amount": "100"})
    for _ in range(2):
        discounted = await manager.run_action(
            "invoices", "recalculate_totals", other.id, {"percent": "10"}, idempotency_key="a"
        )
    assert discounted.amount == 90  # applied once, not twice
    for _ in range(2):
        assert await manager.delete("invoices", other.id, idempotency_key="d") is None
    assert publisher.names().count("ResourceDeleted") == 1


async def test_keys_are_per_caller(store):
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
    )
    await seed(manager, ADMIN)
    data = {"customer": "ACME", "amount": "10"}
    ana = await manager.create("invoices", data, actor=ANA, idempotency_key="same")
    bob = await manager.create("invoices", data, actor=BOB, idempotency_key="same")
    assert ana.id != bob.id


async def test_a_failed_write_keeps_no_key(manager, store):
    with pytest.raises(DomainError):
        await manager.create("invoices", {"customer": "ACME", "amount": "-1"}, idempotency_key="f")
    assert store.tables.get("__idempotency__", {}) == {}
    # the same key works for a corrected retry of the same request? no: it is a new request
    created = await manager.create(
        "invoices", {"customer": "ACME", "amount": "1"}, idempotency_key="f"
    )
    assert created.id


async def test_hidden_fields_stay_hidden_on_replay(store):
    from resource_engine.authz.fields import hidden_fields

    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
    )
    await seed(manager, ADMIN)
    data = {"customer": "ACME", "amount": "10"}
    first = await manager.create("invoices", data, actor=ANA, idempotency_key="h")
    again = await manager.create("invoices", data, actor=ANA, idempotency_key="h")
    assert hidden_fields(first) == hidden_fields(again) == {"notes"}
    assert again.model_dump() == first.model_dump()


async def test_expired_keys_run_again_and_are_purged(store):
    manager = ResourceManager(
        registry, store.unit_of_work, blob_store=InMemoryBlobStore(),
        idempotency_ttl=timedelta(milliseconds=50),
    )  # fmt: skip
    data = {"customer": "ACME", "amount": "10"}
    first = await manager.create("invoices", data, idempotency_key="e")
    await asyncio.sleep(0.1)
    second = await manager.create("invoices", data, idempotency_key="e")
    assert second.id != first.id
    janitor = IdempotencyJanitor(store.unit_of_work)
    assert await janitor.run_once(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert store.tables["__idempotency__"] == {}


async def test_racing_duplicates_run_once(store, publisher):
    from invoices.application.resource import InvoiceResource

    both_started, release = asyncio.Event(), asyncio.Event()
    arrived = []

    async def wait_for_the_other(ctx):
        arrived.append(1)
        if len(arrived) == 2:
            both_started.set()
        await release.wait()

    hooks = InvoiceResource.definition().hooks
    from resource_engine.application.hooks import HookDispatcher, HookKey, HookPoint

    key = HookKey(HookPoint.BEFORE_CREATE)
    patched = {**hooks, key: (*hooks.get(key, ()), wait_for_the_other)}
    definition = InvoiceResource.definition()
    original = definition.dispatcher
    object.__setattr__(definition, "dispatcher", HookDispatcher(patched))
    try:
        manager = ResourceManager(
            registry, store.unit_of_work, event_publisher=publisher, blob_store=InMemoryBlobStore()
        )
        data = {"customer": "ACME", "amount": "10"}
        tasks = [
            asyncio.create_task(manager.create("invoices", data, idempotency_key="race"))
            for _ in range(2)
        ]
        await asyncio.wait_for(both_started.wait(), 1)
        release.set()
        first, second = await asyncio.gather(*tasks)
    finally:
        object.__setattr__(definition, "dispatcher", original)
    assert first.id == second.id
    assert len(store.all("invoices")) == 1
    assert publisher.names() == ["ResourceCreated"]


async def test_inside_a_transaction(manager, store):
    async with manager.transaction():
        await manager.create("invoices", {"customer": "A", "amount": "1"}, idempotency_key="x")
        again = await manager.create(
            "invoices", {"customer": "A", "amount": "1"}, idempotency_key="x"
        )
    assert again.id == 1 and len(store.all("invoices")) == 1


async def test_invalid_key(manager):
    for key in ("", "has space", "x" * 256, "àccent"):
        with pytest.raises(ValidationFailed):
            await manager.create("invoices", {"customer": "A", "amount": "1"}, idempotency_key=key)


async def test_unit_of_work_without_a_store():
    class NoStore:
        def __init__(self, inner):
            self.inner = inner

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self.inner.__aexit__(*exc)

        def repository(self, name):
            return self.inner.repository(name)

        async def commit(self):
            await self.inner.commit()

    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry, lambda: NoStore(store.unit_of_work()), blob_store=InMemoryBlobStore()
    )
    await manager.create("invoices", {"customer": "A", "amount": "1"})  # no key: fine
    with pytest.raises(IdempotencyNotSupported):
        await manager.create("invoices", {"customer": "A", "amount": "1"}, idempotency_key="k")


class TestInMemoryIdempotency(IdempotencyContract):
    @pytest.fixture
    def contract_uow_factory(self):
        return InMemoryStore().unit_of_work
