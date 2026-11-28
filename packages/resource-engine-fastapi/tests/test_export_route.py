"""GET /<resource>/export: the list as a CSV or XLSX file."""

from __future__ import annotations

import io
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.generation import DocumentGenerator, Printout
from resource_engine.templates import StringTemplates
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
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


# ------------------------------------------- background exports and printouts


class PrintedItemResource(ItemResource):
    name = "printed_items"
    printouts = {"label": Printout(formats=("txt", "html"), context=lambda record, _actor: record)}


async def background_client(**mount):  # noqa: ANN003, ANN201
    registry = ResourceRegistry()
    registry.register(PrintedItemResource)
    registry.register_documents()
    templates = StringTemplates({"documents/label.txt": "$name x$qty",
                                 "documents/label.html": "<b>$name</b>"})  # fmt: skip
    manager = ResourceManager(
        registry,
        InMemoryStore.from_registry(registry).unit_of_work,
        blob_store=InMemoryBlobStore(),
        generator=DocumentGenerator(templates),
    )
    for name, qty in (("bolt", 3), ("nut", 10)):
        await manager.create("printed_items", {"name": name, "qty": qty})
    app = FastAPI()
    mount_resources(app, registry, manager, prefix="/api", **mount)
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
    return api, manager


async def test_a_background_export_over_http():
    api, manager = await background_client()
    started = await api.post("/api/printed_items/exports", params={"name": "nut"})
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["status"] == "pending" and job["rows"] == 1
    location = started.headers["location"]
    assert location == f"/api/exports/{job['id']}"
    early = await api.get(f"{location}/content")
    assert early.status_code == 409 and early.json()["error"]["code"] == "RE-E605"
    await manager.exports.run_once()
    assert (await api.get(location)).json()["status"] == "done"
    file = await api.get(f"{location}/content")
    assert file.status_code == 200 and file.headers["content-type"].startswith("text/csv")
    assert file.content.decode("utf-8-sig").splitlines() == ["name,qty,id", "nut,10,2"]
    assert (await api.get("/api/exports/" + "0" * 32)).status_code == 404
    paths = (await api.get("/openapi.json")).json()["paths"]
    assert "/api/printed_items/exports" in paths and "/api/exports/{export_id}/content" in paths


async def test_a_large_export_points_to_the_background_one():
    api, manager = await background_client()
    from resource_engine import exports

    original = exports.export_table

    async def tiny(*args, **kw):  # noqa: ANN002, ANN003, ANN202
        return await original(*args, **{**kw, "max_rows": 1})

    exports.export_table = tiny
    try:
        refused = await api.get("/api/printed_items/export")
    finally:
        exports.export_table = original
    assert refused.status_code == 400
    assert "POST /printed_items/exports" in refused.json()["error"]["hint"]


async def test_printouts_are_rendered_and_not_stored():
    api, manager = await background_client()
    text = await api.get("/api/printed_items/1/printouts/label")
    assert text.status_code == 200 and text.text == "bolt x3"
    assert 'filename="label-1.txt"' in text.headers["content-disposition"]
    html = await api.get("/api/printed_items/2/printouts/label", params={"format": "html"})
    assert html.text == "<b>nut</b>" and html.headers["content-type"].startswith("text/html")
    assert (await api.get("/api/printed_items/1/printouts/label?format=pdf")).status_code == 422
    assert (await api.get("/api/printed_items/9/printouts/label")).status_code == 404
    assert not manager.documents.store.blobs  # nothing stored
