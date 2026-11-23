from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Action, ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import (
    ConfigurationError,
    VersionConflict,
    VersioningNotSupported,
    VersionRequired,
)
from resource_engine.testing import InMemoryStore
from statemachine import State, StateMachine


@dataclass
class Doc:
    title: str
    status: str | None = None
    version: int | None = None
    id: int | None = None


class DocMachine(StateMachine):
    draft = State(initial=True)
    done = State(final=True)
    finish = draft.to(done)


class DocIn(BaseModel):
    title: str


class DocPatch(BaseModel):
    title: str | None = None
    version: int | None = None


class DocOut(DocIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    version: int


class Rename(Action):
    name = "rename"
    instance = True

    class Input(BaseModel):
        title: str

    input_schema = Input

    async def execute(self, ctx):
        ctx.entity.title = ctx.data.title
        await ctx.repository().update(ctx.entity)  # actions may write themselves


class DocResource(Resource):
    model = Doc
    schemas = {"create": DocIn, "update": DocPatch, "read": DocOut}
    operations = ["list", "get", "create", "update", "delete"]
    state_machine = DocMachine
    actions = [Rename]
    version_field = "version"


def build(cls=DocResource, *, audit=False):
    registry = ResourceRegistry()
    registry.register(cls)
    if audit:
        registry.register_audit()
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work, audit=audit), store


async def test_every_write_checks_and_bumps_once():
    manager, _ = build()
    doc = await manager.create("docs", {"title": "a"})
    assert doc.version == 1
    doc = await manager.update("docs", doc.id, {"title": "b"}, expected_version=1)
    assert doc.version == 2
    doc = await manager.run_action("docs", "rename", doc.id, {"title": "c"}, expected_version=2)
    assert doc.version == 3 and doc.title == "c"
    doc = await manager.transition("docs", doc.id, "finish", expected_version=3)
    assert doc.version == 4 and doc.status == "done"
    doc = await manager.update("docs", doc.id, {"title": "d"})  # no version: allowed
    assert doc.version == 5
    await manager.delete("docs", doc.id, expected_version=5)


async def test_stale_writes_are_refused_and_audited():
    manager, _ = build(audit=True)
    doc = await manager.create("docs", {"title": "a"})
    await manager.update("docs", doc.id, {"title": "b"})
    for write in (
        manager.update("docs", doc.id, {"title": "x"}, expected_version=1),
        manager.update("docs", doc.id, {"title": "x", "version": 1}),  # body field
        manager.transition("docs", doc.id, "finish", expected_version=1),
        manager.run_action("docs", "rename", doc.id, {"title": "x"}, expected_version=1),
        manager.delete("docs", doc.id, expected_version=1),
    ):
        with pytest.raises(VersionConflict) as caught:
            await write
        assert caught.value.details["current"] == 2
    current = await manager.get("docs", doc.id)
    assert (current.title, current.version) == ("b", 2)
    log = await manager.list("audit_log", ListQuery(filters={"outcome": "denied"}, order_by=["at"]))
    assert [e.details["code"] for e in log.items] == ["RE-E105"] * 5
    assert log.items[0].details["expected"] == 1 and log.items[0].details["current"] == 2


async def test_the_body_version_is_not_written():
    manager, _ = build()
    doc = await manager.create("docs", {"title": "a"})
    doc = await manager.update("docs", doc.id, {"title": "b", "version": 1})
    assert doc.version == 2


async def test_interleaved_units_of_work_conflict_on_commit():
    manager, store = build()
    doc = await manager.create("docs", {"title": "a"})
    first, second = store.unit_of_work(), store.unit_of_work()
    async with first, second:
        for uow, title in ((first, "one"), (second, "two")):
            repo = uow.repository("docs")
            entity = await repo.get(doc.id)
            entity.title = title
            await repo.update(entity, expected_version=1)
        await first.commit()
        with pytest.raises(VersionConflict):
            await second.commit()
    assert (await manager.get("docs", doc.id)).title == "one"


async def test_required_versions():
    class Strict(DocResource):
        name = "strict"
        require_version = True

    manager, _ = build(Strict)
    doc = await manager.create("strict", {"title": "a"})
    with pytest.raises(VersionRequired):
        await manager.update("strict", doc.id, {"title": "b"})
    assert (await manager.update("strict", doc.id, {"title": "b"}, expected_version=1)).version == 2


async def test_repositories_without_versions_are_refused():
    class Plain:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        supports_versions = False

    manager, store = build()
    doc = await manager.create("docs", {"title": "a"})

    class Uow:
        def __init__(self):
            self.inner = store.unit_of_work()

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self.inner.__aexit__(*exc)

        def repository(self, name):
            return Plain(self.inner.repository(name))

        async def commit(self):
            await self.inner.commit()

    manager.uow_factory = Uow
    with pytest.raises(VersioningNotSupported):
        await manager.update("docs", doc.id, {"title": "b"})


def test_definition_rules():
    class NotRead(DocResource):
        name = "not_read"

        class Out(DocIn):
            model_config = ConfigDict(from_attributes=True)
            id: int
            status: str

        schemas = {"create": DocIn, "update": DocPatch, "read": Out}

    class InCreate(DocResource):
        name = "in_create"

        class In(DocIn):
            version: int | None = None

        schemas = {"create": In, "update": DocPatch, "read": DocOut}

    class StrictWithout(Resource):
        model = Doc
        schemas = {"create": DocIn, "read": DocOut}
        operations = ["get"]
        require_version = True

    for cls in (NotRead, InCreate, StrictWithout):
        with pytest.raises(ConfigurationError):
            cls.definition()
