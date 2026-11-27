"""Composite unit of work: SQL-like resources and a remote one in one
operation, with compensations for the remote writes."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Page, Resource, ResourceManager, ResourceRegistry
from resource_engine.composite import CompositeStore
from resource_engine.testing import InMemoryStore


@dataclass
class Invoice:
    number: str
    id: int | None = None


@dataclass
class Partner:
    name: str
    id: int | None = None


class InvoiceIn(BaseModel):
    number: str


class PartnerIn(BaseModel):
    name: str


class PartnerUpdate(BaseModel):
    name: str | None = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str


class PartnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class InvoiceResource(Resource):
    model = Invoice
    schemas = {"create": InvoiceIn, "read": InvoiceOut}
    operations = ["list", "get", "create"]


class PartnerResource(Resource):
    model = Partner
    schemas = {"create": PartnerIn, "update": PartnerUpdate, "read": PartnerOut}
    operations = ["list", "get", "create", "update", "delete"]


class RemoteRepository:
    """A remote API: every write takes effect at once."""

    def __init__(self, rows: dict[int, Partner], fail_on: set[str]) -> None:
        self.rows = rows
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def get(self, entity_id):
        row = self.rows.get(entity_id)
        return dataclasses.replace(row) if row else None

    async def list(self, query):
        items = list(self.rows.values())
        return Page(items, len(items), query.offset, query.limit)

    async def add(self, entity):
        self.calls.append("add")
        entity.id = entity.id or max(self.rows, default=0) + 1
        self.rows[entity.id] = dataclasses.replace(entity)
        return entity

    async def update(self, entity, **kw):
        self.calls.append("update")
        self.rows[entity.id] = dataclasses.replace(entity)
        return entity

    async def delete(self, entity, **kw):
        self.calls.append("delete")
        if "delete" in self.fail_on:
            raise RuntimeError("remote delete failed")
        self.rows.pop(entity.id, None)


class RemoteUnitOfWork:
    transactional = False

    def __init__(self, repository: RemoteRepository) -> None:
        self.repo = repository

    def repository(self, name):
        return self.repo

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def build(*, fail_on=(), compensations=None):
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    registry.register(PartnerResource)
    local = InMemoryStore.from_registry(registry)
    remote = RemoteRepository({}, set(fail_on))
    store = CompositeStore(
        local.unit_of_work,
        {"partners": lambda: RemoteUnitOfWork(remote)},
        compensations=compensations,
        registry=registry,
    )
    return ResourceManager(registry, store.unit_of_work), local, remote


async def test_routes_each_resource_and_commits():
    manager, local, remote = build()
    async with manager.transaction():
        await manager.create("invoices", {"number": "F-1"})
        await manager.create("partners", {"name": "Acme"})
    assert [i.number for i in (await manager.list("invoices")).items] == ["F-1"]
    assert [p.name for p in remote.rows.values()] == ["Acme"]
    assert remote.calls == ["add"]


async def test_failure_compensates_remote_writes_in_reverse():
    manager, local, remote = build()
    acme = await manager.create("partners", {"name": "Acme"})
    with pytest.raises(RuntimeError, match="boom"):
        async with manager.transaction():
            await manager.update("partners", acme.id, {"name": "Acme SA"})
            await manager.create("partners", {"name": "Beta"})
            await manager.create("invoices", {"number": "F-1"})
            raise RuntimeError("boom")
    assert {p.name for p in remote.rows.values()} == {"Acme"}  # Beta deleted, Acme restored
    assert remote.calls == ["add", "update", "add", "delete", "update"]
    assert (await manager.list("invoices")).items == []


async def test_failed_operation_compensates_its_own_remote_write():
    manager, _, remote = build()
    with pytest.raises(Exception):  # noqa: B017 - validation of the invoice fails
        async with manager.transaction():
            await manager.create("partners", {"name": "Acme"})
            await manager.create("invoices", {})
    assert remote.rows == {}


async def test_primary_commit_failure_compensates(monkeypatch):
    manager, local, remote = build()
    from resource_engine.testing.memory import InMemoryUnitOfWork

    async def broken(self):
        raise RuntimeError("database down")

    monkeypatch.setattr(InMemoryUnitOfWork, "commit", broken)
    with pytest.raises(RuntimeError, match="database down"):
        await manager.create("partners", {"name": "Acme"})
    assert remote.rows == {}


async def test_custom_compensation_and_best_effort():
    cancelled = []

    async def cancel(repository, before, after):
        cancelled.append(after.name)

    manager, _, remote = build(fail_on={"delete"}, compensations={"partners": {"add": cancel}})
    with pytest.raises(RuntimeError, match="boom"):
        async with manager.transaction():
            await manager.create("partners", {"name": "Acme"})
            raise RuntimeError("boom")
    assert cancelled == ["Acme"]
    assert "delete" not in remote.calls

    manager, _, remote = build(fail_on={"delete"})
    with pytest.raises(RuntimeError, match="boom"):
        async with manager.transaction():
            await manager.create("partners", {"name": "Acme"})
            await manager.create("partners", {"name": "Beta"})
            raise RuntimeError("boom")
    assert remote.calls == ["add", "add", "delete", "delete"]  # both tried, both failed


async def test_commit_keeps_remote_writes_and_clears_the_log():
    manager, _, remote = build()
    created = await manager.create("partners", {"name": "Acme"})
    await manager.delete("partners", created.id)
    assert remote.rows == {} and remote.calls == ["add", "delete"]
