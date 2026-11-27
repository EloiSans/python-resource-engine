"""Capability flags: queries a store can't run are refused with RE-E103
before they reach it; ``capability_problems`` reports declarations beyond
the store; ``without_not`` rewrites row filters for stores without NOT."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz.conditions import matches
from resource_engine.capabilities import capabilities, capability_problems
from resource_engine.errors import QueryNotSupported
from resource_engine.nosql import DocumentCodec, without_not
from resource_engine.testing import InMemoryStore


@dataclass
class Partner:
    name: str
    city: str = ""
    version: int = 1
    id: int | None = None


class PartnerIn(BaseModel):
    name: str
    city: str = ""


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    city: str
    version: int


class PartnerResource(Resource):
    model = Partner
    schemas = {"create": PartnerIn, "read": PartnerOut}
    operations = ["list", "create"]
    filterable = ["name", "city"]
    sortable = ["name", "city"]
    version_field = "version"


def build():
    registry = ResourceRegistry()
    registry.register(PartnerResource)
    store = InMemoryStore.from_registry(registry)
    return registry, store


class Indexed:
    """A store with an index on ``city`` only, and no versions."""

    def __init__(self, inner):
        self.inner = inner
        self.filterable = frozenset({"city"})
        self.sortable = frozenset({"city"})
        self.max_offset = 100
        self.supports_conditions = True
        self.supports_versions = False

    def __getattr__(self, name):
        return getattr(self.inner, name)


class IndexedUnitOfWork:
    transaction_scope = "partition"
    max_writes = 100

    def __init__(self, inner):
        self.inner = inner

    def repository(self, name):
        return Indexed(self.inner.repository(name))

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.inner.__aexit__(*exc)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def test_the_manager_refuses_what_the_store_cannot_run():
    registry, store = build()
    manager = ResourceManager(registry, lambda: IndexedUnitOfWork(store.unit_of_work()))
    await manager.create("partners", {"name": "Acme", "city": "Girona"})
    page = await manager.list("partners", ListQuery(filters={"city": "Girona"}, order_by=["-city"]))
    assert [p.name for p in page.items] == ["Acme"]
    with pytest.raises(QueryNotSupported) as raised:
        await manager.list("partners", ListQuery(filters={"name": "Acme"}, order_by=["name"]))
    assert raised.value.code == "RE-E103"
    assert raised.value.details["filters"] == ["name"]
    assert raised.value.details["order_by"] == ["name"]
    with pytest.raises(QueryNotSupported, match="offset 100"):
        await manager.list("partners", ListQuery(offset=101))


async def test_capabilities_and_problems():
    registry, store = build()
    async with store.unit_of_work() as uow:
        caps = capabilities(uow.repository("partners"), uow)
        assert caps.conditions and caps.versions and caps.filterable is None
        assert caps.transaction_scope == "unit_of_work"
        assert capability_problems(registry, uow) == []
    async with IndexedUnitOfWork(store.unit_of_work()) as uow:
        caps = capabilities(uow.repository("partners"), uow)
        assert caps.transaction_scope == "partition" and caps.max_writes == 100
        assert capability_problems(registry, uow) == [
            "partners: filterable ['name'] not indexed by the store",
            "partners: sortable ['name'] not indexed by the store",
            "partners: versioned, but the store can't compare versions",
        ]


@pytest.mark.parametrize(
    "condition",
    [
        {"not": {"lt": [{"attr": "resource.version"}, 2]}},
        {"not": {"in": [{"attr": "resource.name"}, ["a", "b"]]}},
        {
            "not": {
                "and": [
                    {"eq": [{"attr": "resource.name"}, "a"]},
                    {"ge": [{"attr": "resource.version"}, 2]},
                ]
            }
        },
        {
            "not": {
                "or": [
                    {"eq": [{"attr": "resource.name"}, "a"]},
                    {"not": {"eq": [{"attr": "resource.city"}, "x"]}},
                ]
            }
        },
    ],
)
def test_without_not_keeps_the_meaning(condition):
    rewritten = without_not(condition)
    assert "not" not in str(rewritten)
    for partner in [Partner("a", "x", 1), Partner("b", "y", 2), Partner("c", "x", 3)]:
        assert matches(rewritten, partner) == matches(condition, partner)
    assert without_not({"not": {"exists": {"attr": "resource.city"}}}) == {
        "not": {"exists": {"attr": "resource.city"}}
    }


def test_codec_ids_and_round_trip():
    codec = DocumentCodec(Partner)
    assert isinstance(codec.new_id(), int) and codec.new_id() < 2**53
    partner = Partner("Acme", "Girona", 3, 7)
    assert codec.decode(codec.encode(partner)) == partner
    assert codec.decode({"name": "Acme", "id": "7"}).id == 7  # Cosmos ids come back as text
