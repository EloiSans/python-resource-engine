from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, Resource, ResourceManager, ResourceRegistry
from resource_engine.ports.storage import PresignedRequest
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_fastapi import mount_resources
from statemachine import State, StateMachine


@dataclass
class Claim:
    title: str
    status: str | None = None
    id: int | None = None


class ClaimMachine(StateMachine):
    open = State(initial=True)
    submitted = State(final=True)
    submit = open.to(submitted)


class ClaimIn(BaseModel):
    title: str


class ClaimOut(ClaimIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class ClaimResource(Resource):
    model = Claim
    schemas = {"create": ClaimIn, "read": ClaimOut}
    operations = ["get", "create"]
    state_machine = ClaimMachine
    documents = [
        DocumentSlot(
            "receipt",
            content_types=["application/pdf", "image/*"],
            max_bytes=64,
            required_for=["submit"],
            upload="api",
        ),
        DocumentSlot("evidence", multiple=True, upload="presigned"),
    ]


def make_client(redirect: bool = False):
    registry = ResourceRegistry()
    registry.register(ClaimResource)
    registry.register_documents()
    blobs = InMemoryBlobStore()
    manager = ResourceManager(
        registry, InMemoryStore.from_registry(registry).unit_of_work, blob_store=blobs
    )
    app = FastAPI()
    mount_resources(app, registry, manager, redirect_downloads=redirect)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
    return client, blobs


@pytest.fixture
def api():
    return make_client()


async def test_upload_list_download_and_gate(api):
    client, _ = api
    claim = (await client.post("/claims", json={"title": "Taxi"})).json()
    r = await client.post(f"/claims/{claim['id']}/transitions/submit")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RE-E606"

    files = {"file": ("rebut è.pdf", b"%PDF-1.7", "application/pdf")}
    r = await client.post(f"/claims/{claim['id']}/documents/receipt", files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "available" and doc["size"] == 8

    listed = (await client.get(f"/claims/{claim['id']}/documents")).json()
    assert [d["id"] for d in listed] == [doc["id"]]
    content = await client.get(f"/documents/{doc['id']}/content")
    assert content.content == b"%PDF-1.7"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8''rebut%20%C3%A8.pdf" in content.headers["content-disposition"]

    assert (await client.post(f"/claims/{claim['id']}/transitions/submit")).status_code == 200
    r = await client.delete(f"/claims/{claim['id']}/documents/{doc['id']}")
    assert r.status_code == 204
    assert (await client.get(f"/documents/{doc['id']}/content")).status_code == 409


async def test_limits_and_unknown_slot(api):
    client, _ = api
    cid = (await client.post("/claims", json={"title": "x"})).json()["id"]
    r = await client.post(
        f"/claims/{cid}/documents/receipt", files={"file": ("a.txt", b"hi", "text/plain")}
    )
    assert r.status_code == 415
    r = await client.post(
        f"/claims/{cid}/documents/receipt", files={"file": ("a.pdf", b"x" * 100, "application/pdf")}
    )
    assert r.status_code == 413
    r = await client.post(
        f"/claims/{cid}/documents/nope", files={"file": ("a.pdf", b"x", "application/pdf")}
    )
    assert r.status_code == 422  # not in the slot enum


async def test_presigned_flow_and_hidden_document_transitions(api):
    client, blobs = api
    cid = (await client.post("/claims", json={"title": "x"})).json()["id"]
    r = await client.post(
        f"/claims/{cid}/documents/evidence/uploads",
        json={"filename": "photo.jpg", "content_type": "image/jpeg"},
    )
    assert r.status_code == 201, r.text
    ticket = r.json()
    request = PresignedRequest(**ticket["upload"])
    await blobs.upload(request, b"jpeg bytes")
    doc_id = ticket["document"]["id"]
    confirmed = await client.post(f"/documents/{doc_id}/confirm")
    assert confirmed.json()["status"] == "available"
    assert (await client.get(f"/documents/{doc_id}")).json()["size"] == 10
    # document transitions are internal
    assert (await client.post(f"/documents/{doc_id}/transitions/discard")).status_code == 404
    openapi = (await client.get("/openapi.json")).json()
    assert "/claims/{entity_id}/documents/{slot}/uploads" in openapi["paths"]


async def test_redirect_downloads():
    client, _ = make_client(redirect=True)
    cid = (await client.post("/claims", json={"title": "x"})).json()["id"]
    doc = (
        await client.post(
            f"/claims/{cid}/documents/receipt", files={"file": ("r.png", b"png", "image/png")}
        )
    ).json()
    r = await client.get(f"/documents/{doc['id']}/content")
    assert r.status_code == 307 and r.headers["location"].startswith("memory://get/")
