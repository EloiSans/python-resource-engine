"""Translatable fields: one text per language, read, written, filtered and
sorted in the request's language."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.i18n import use_locale
from resource_engine.testing import InMemoryStore


@dataclass
class Product:
    sku: str
    name: dict[str, str] = field(default_factory=dict)
    id: int | None = None


class ProductIn(BaseModel):
    sku: str
    name: str | None = None
    name_translations: dict[str, str | None] | None = None


class ProductUpdate(BaseModel):
    name: str | None = None
    name_translations: dict[str, str | None] | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku: str
    name: str | None
    name_translations: dict[str, str] = {}


class ProductResource(Resource):
    model = Product
    schemas = {"create": ProductIn, "update": ProductUpdate, "read": ProductOut}
    operations = ["list", "get", "create", "update"]
    filterable = ["name"]
    sortable = ["name"]
    translatable = ["name"]
    default_locale = "ca"


def build(uow_factory=None):
    registry = ResourceRegistry()
    registry.register(ProductResource)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, uow_factory or store.unit_of_work)


async def check(manager):
    chair = await manager.create("products", {"sku": "C1", "name": "Cadira"})  # no locale: ca
    assert chair.name == "Cadira" and chair.name_translations == {"ca": "Cadira"}
    with use_locale("en"):
        updated = await manager.update("products", chair.id, {"name": "Chair"})
        assert updated.name == "Chair"
    with use_locale("en-GB"):
        assert (await manager.get("products", chair.id)).name == "Chair"  # en-GB falls back to en
    with use_locale("es"):
        assert (await manager.get("products", chair.id)).name == "Cadira"  # default: ca
        await manager.create(
            "products",
            {"sku": "T1", "name_translations": {"es": "Mesa", "en": "Table", "ca": "Taula"}},
        )
    both = await manager.update(
        "products", chair.id, {"name_translations": {"es": "Silla", "en": None}}
    )
    assert both.name_translations == {"ca": "Cadira", "es": "Silla"}  # en removed
    with use_locale("en"):
        assert (await manager.get("products", chair.id)).name == "Cadira"  # no en: ca
        found = await manager.list("products", ListQuery(filters={"name": "Table"}))
        assert [p.sku for p in found.items] == ["T1"]
    with use_locale("es"):
        ordered = await manager.list("products", ListQuery(order_by=["name"]))
        assert [p.name for p in ordered.items] == ["Mesa", "Silla"]
    with use_locale("ca"):
        ordered = await manager.list("products", ListQuery(order_by=["-name"]))
        assert [p.name for p in ordered.items] == ["Taula", "Cadira"]


async def test_in_memory():
    await check(build())


@pytest.fixture
async def sqlite(tmp_path):
    from resource_engine_sqlalchemy import unit_of_work_factory
    from sqlalchemy import JSON, Column, Integer, MetaData, String, Table
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import registry as orm_registry

    metadata = MetaData()
    mapper = orm_registry(metadata=metadata)
    table = Table(
        "translated_products",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("sku", String(20)),
        Column("name", JSON),
    )
    mapper.map_imperatively(Product, table)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(ProductResource)
    yield unit_of_work_factory(async_sessionmaker(engine, expire_on_commit=False), registry)
    await engine.dispose()
    mapper.dispose()


async def test_on_sql_with_a_json_column(sqlite):
    await check(build(sqlite))
