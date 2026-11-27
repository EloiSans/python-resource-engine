"""GET /<resource>/export: the list as a CSV or XLSX file."""

from __future__ import annotations

import io
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore
from resource_engine_fastapi import mount_resources


@dataclass
class Item:
    name: str
    qty: int
    id: int | None = None


class ItemIn(BaseModel):
    name: str
    qty: int


class ItemOut(ItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ItemResource(Resource):
    model = Item
    schemas = {"create": ItemIn, "read": ItemOut}
    operations = ["list", "get", "create"]
    filterable = ["name"]
    sortable = ["id", "qty"]


async def client() -> httpx.AsyncClient:
    registry = ResourceRegistry()
    registry.register(ItemResource)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    for name, qty in (("bolt", 3), ("nut", 10), ("bolt", 7)):
        await manager.create("items", {"name": name, "qty": qty})
    app = FastAPI()
    mount_resources(app, registry, manager)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_csv_and_xlsx_with_the_list_filters():
    api = await client()
    csv = await api.get("/items/export", params={"name": "bolt", "order_by": "-qty"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert 'filename="items-' in csv.headers["content-disposition"]
    assert csv.content.decode("utf-8-sig").splitlines() == ["name,qty,id", "bolt,7,3", "bolt,3,1"]
    xlsx = await api.get("/items/export", params={"format": "xlsx"})
    rows = list(load_workbook(io.BytesIO(xlsx.content)).active.iter_rows(values_only=True))
    assert rows == [("name", "qty", "id"), ("bolt", 3, 1), ("nut", 10, 2), ("bolt", 7, 3)]
    assert (await api.get("/items/export", params={"format": "pdf"})).status_code == 422
    assert (await api.get("/items/1")).json()["name"] == "bolt"  # /{id} still works
    assert "/items/export" in (await api.get("/openapi.json")).json()["paths"]
