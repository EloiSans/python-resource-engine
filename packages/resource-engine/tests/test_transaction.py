from __future__ import annotations

import contextlib
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import EntityNotFound, TransactionDoomed, ValidationFailed
from resource_engine.messaging import OutboxDelivery
from resource_engine.testing import InMemoryStore, RecordingEventPublisher


@dataclass
class Item:
    name: str
    id: int | None = None


class ItemIn(BaseModel):
    name: str


class ItemOut(ItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


calls: list[str] = []


def staged(ctx):
    ctx.on_rollback(lambda: calls.append(f"undo {ctx.entity.name}"))
    ctx.on_commit(lambda: calls.append(f"commit {ctx.entity.name}"))


def after(ctx):
    calls.append(f"after {ctx.entity.name}")


class ItemResource(Resource):
    model = Item
    schemas = {"create": ItemIn, "update": ItemIn, "read": ItemOut}
    operations = ["list", "get", "create", "update"]
    hooks = {"after_create": staged, "after_commit": after}
    sortable = ["id"]


@pytest.fixture(autouse=True)
def clear():
    calls.clear()


def build(**kw):
    registry = ResourceRegistry()
    registry.register(ItemResource)
    registry.register_audit()
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work, audit=True, **kw), store


async def names(manager):
    return [i.name for i in (await manager.list("items", ListQuery(order_by=["id"]))).items]


async def test_operations_commit_together_and_post_commit_work_waits():
    publisher = RecordingEventPublisher()
    manager, _ = build(event_publisher=publisher)
    async with manager.transaction() as tx:
        a = await manager.create("items", {"name": "a"})
        await manager.update("items", a.id, {"name": "b"})
        assert (await manager.get("items", a.id)).name == "b"  # reads join
        async with manager.transaction() as inner:  # nested: joins
            assert inner is tx
            await manager.create("items", {"name": "c"})
        assert calls == [] and publisher.events == []
    assert await names(manager) == ["b", "c"]
    assert calls == ["commit b", "after b", "after b", "commit c", "after c"]  # same entity
    assert [type(e).__name__ for e in publisher.events] == [
        "ResourceCreated",
        "ResourceUpdated",
        "ResourceCreated",
    ]
    log = await manager.list("audit_log", ListQuery(filters={"resource": "items"}))
    assert log.total == 3


async def test_an_error_rolls_everything_back():
    manager, _ = build()
    with pytest.raises(RuntimeError):
        async with manager.transaction():
            await manager.create("items", {"name": "a"})
            await manager.create("items", {"name": "b"})
            raise RuntimeError("boom")
    assert await names(manager) == []
    assert calls == ["undo b", "undo a"]
    log = await manager.list("audit_log", ListQuery(filters={"resource": "items"}))
    assert log.total == 0


async def test_a_caught_failure_dooms_the_transaction():
    manager, _ = build()
    with pytest.raises(TransactionDoomed) as caught:
        async with manager.transaction():
            await manager.create("items", {"name": "a"})
            with contextlib.suppress(EntityNotFound):
                await manager.update("items", 99, {"name": "x"})
    assert caught.value.details["code"] == "RE-E102"
    assert isinstance(caught.value.__cause__, EntityNotFound)
    assert await names(manager) == []


async def test_failed_reads_and_early_refusals_do_not_doom():
    manager, _ = build()
    async with manager.transaction():
        await manager.create("items", {"name": "a"})
        with pytest.raises(EntityNotFound):
            await manager.get("items", 99)
        with pytest.raises(ValidationFailed):  # before the unit of work
            await manager.create("items", {})
    assert await names(manager) == ["a"]


async def test_outbox_events_share_the_commit():
    manager, store = build(event_delivery=OutboxDelivery())
    with pytest.raises(RuntimeError):
        async with manager.transaction():
            await manager.create("items", {"name": "a"})
            raise RuntimeError
    assert store.outbox_records() == []
    async with manager.transaction():
        await manager.create("items", {"name": "a"})
    assert len(store.outbox_records()) == 1


async def test_other_managers_are_independent():
    first, _ = build()
    second, _ = build()
    with pytest.raises(RuntimeError):
        async with first.transaction():
            await second.create("items", {"name": "kept"})
            raise RuntimeError
    assert await names(second) == ["kept"]
