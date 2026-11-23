"""Compare-and-set in SQL: a stale write fails instead of overwriting."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import VersionConflict
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Card:
    title: str
    version: int | None = None
    id: int | None = None


class CardIn(BaseModel):
    title: str


class CardPatch(BaseModel):
    title: str | None = None


class CardOut(CardIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int


class CardResource(Resource):
    model = Card
    schemas = {"create": CardIn, "update": CardPatch, "read": CardOut}
    operations = ["get", "create", "update", "delete"]
    version_field = "version"


metadata = MetaData()
orm_registry(metadata=metadata).map_imperatively(
    Card,
    Table(
        "cards",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("title", String(50)),
        Column("version", Integer, nullable=False, default=1),
    ),
)


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'versions.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(CardResource)
    uow = unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    yield ResourceManager(registry, uow)
    await engine.dispose()


async def test_second_writer_loses(manager):
    card = await manager.create("cards", {"title": "a"})
    assert card.version == 1
    first, second = manager.uow_factory(), manager.uow_factory()
    async with first, second:
        mine = await first.repository("cards").get(card.id)
        theirs = await second.repository("cards").get(card.id)
        mine.title = "mine"
        await first.repository("cards").update(mine, expected_version=1)
        await first.commit()
        theirs.title = "theirs"
        with pytest.raises(VersionConflict) as caught:
            await second.repository("cards").update(theirs, expected_version=1)
        assert caught.value.details["current"] == 2
    saved = await manager.get("cards", card.id)
    assert (saved.title, saved.version) == ("mine", 2)


async def test_manager_writes_in_sql(manager):
    card = await manager.create("cards", {"title": "a"})
    card = await manager.update("cards", card.id, {"title": "b"}, expected_version=1)
    assert card.version == 2
    with pytest.raises(VersionConflict):
        await manager.update("cards", card.id, {"title": "c"}, expected_version=1)
    with pytest.raises(VersionConflict):
        await manager.delete("cards", card.id, expected_version=1)
    await manager.delete("cards", card.id, expected_version=2)
    async with manager.uow_factory() as uow:
        assert await uow.repository("cards").get(card.id) is None
