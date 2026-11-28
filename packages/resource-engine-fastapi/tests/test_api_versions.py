"""API versions side by side: /v1 converts to and from the current schemas,
deprecated routes are marked in OpenAPI and answer with Deprecation headers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import ConfigurationError
from resource_engine.testing import InMemoryStore
from resource_engine.versions import ApiVersion, Deprecation
from resource_engine_fastapi import mount_resources

SUNSET = Deprecation(
    since=date(2026, 12, 1), sunset=date(2027, 6, 30), link="https://docs.example.com/v2"
)


@dataclass
class Invoice:
    number: str
    total: Decimal
    currency: str = "EUR"
    id: int | None = None


class InvoiceIn(BaseModel):
    number: str
    total: Decimal
    currency: str = "EUR"


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    total: Decimal
    currency: str
    legacy_code: str | None = Field(None, deprecated="Use number.")


class InvoiceInV1(BaseModel):
    number: str
    amount: str


class InvoiceOutV1(BaseModel):
    id: int
    number: str
    amount: str


class InvoiceResource(Resource):
    model = Invoice
    schemas = {"create": InvoiceIn, "read": InvoiceOut}
    operations = ["list", "get", "create"]
    api_versions = {
        "v1": ApiVersion(
            read=InvoiceOutV1,
            create=InvoiceInV1,
            to_read=lambda out: {"id": out["id"], "number": out["number"], "amount": out["total"]},
            from_create=lambda data: {"number": data["number"], "total": data["amount"]},
            deprecation=SUNSET,
        )
    }


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    text: str


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "create"]


def app_with(**options) -> FastAPI:
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    registry.register(NoteResource)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    app = FastAPI()
    mount_resources(app, registry, manager, **options)
    return app


@pytest.fixture
def app():
    return app_with(versions=["v1", "v2"])


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_both_versions_serve_the_same_records(app):
    async with client(app) as http:
        created = await http.post("/v1/invoices", json={"number": "F-1", "amount": "12.50"})
        assert created.status_code == 201
        assert created.json() == {"id": created.json()["id"], "number": "F-1", "amount": "12.50"}
        assert created.headers["deprecation"] == "@1796083200"
        assert created.headers["sunset"] == "Wed, 30 Jun 2027 00:00:00 GMT"
        assert created.headers["link"] == '<https://docs.example.com/v2>; rel="deprecation"'
        current = await http.get(f"/v2/invoices/{created.json()['id']}")
        assert current.json()["total"] == "12.50" and current.json()["currency"] == "EUR"
        assert "deprecation" not in current.headers
        page = (await http.get("/v1/invoices")).json()
        assert page["items"] == [created.json()] and page["total"] == 1
        # a resource without an older version is served unchanged under /v1
        assert (await http.post("/v1/notes", json={"text": "hi"})).status_code == 201
        assert (await http.get("/v2/notes")).json()["total"] == 1


async def test_openapi_marks_deprecated_routes(app):
    spec = app.openapi()
    v1 = spec["paths"]["/v1/invoices"]["post"]
    assert v1["deprecated"] is True
    assert v1["x-deprecation"] == {
        "since": "2026-12-01",
        "sunset": "2027-06-30",
        "link": "https://docs.example.com/v2",
    }
    assert "removed on 2027-06-30" in v1["description"]
    assert "deprecated" not in spec["paths"]["/v2/invoices"]["post"]
    assert "deprecated" not in spec["paths"]["/v1/notes"]["post"]
    assert spec["components"]["schemas"]["InvoiceOut"]["properties"]["legacy_code"]["deprecated"]


async def test_whole_versions_and_unknown_ones():
    app = app_with(versions=["v1", "v2"], deprecated={"v1": Deprecation(reason="Use v2.")})
    async with client(app) as http:
        response = await http.get("/v1/notes")
        assert response.headers["deprecation"] == "true"
    assert app.openapi()["paths"]["/v1/notes"]["get"]["deprecated"] is True
    with pytest.raises(ConfigurationError):
        app_with(versions=["v2"], deprecated={"v1": Deprecation()})


async def test_without_versions_nothing_changes():
    app = app_with()
    async with client(app) as http:
        assert (
            await http.post("/invoices", json={"number": "F-1", "total": "1"})
        ).status_code == 201
        assert "deprecation" not in (await http.get("/invoices")).headers
