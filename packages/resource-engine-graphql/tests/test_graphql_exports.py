"""Background exports from GraphQL: exportNotes queues one, export(id) follows it."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_graphql import GraphQLAdapter


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "get", "create"]
    filterable = ["text"]


def build(blobs: bool) -> tuple[GraphQLAdapter, ResourceManager]:
    registry = ResourceRegistry()
    registry.register(NoteResource)
    if blobs:
        registry.register_documents()
    manager = ResourceManager(
        registry,
        InMemoryStore.from_registry(registry).unit_of_work,
        blob_store=InMemoryBlobStore() if blobs else None,
    )
    return GraphQLAdapter(registry, manager), manager


async def test_export_mutation_and_status_query():
    graphql, manager = build(blobs=True)
    for text in ("a", "b", "a"):
        await manager.create("notes", {"text": text})
    started = await graphql.execute(
        'mutation { exportNotes(format: XLSX, filter: {text: "a"}) { id status rows format } }'
    )
    assert "errors" not in started, started
    job = started["data"]["exportNotes"]
    assert job["status"] == "pending" and job["rows"] == 2 and job["format"] == "XLSX"
    await manager.exports.run_once()
    status = await graphql.execute(
        "query($id: String!) { export(id: $id) { status filename contentPath downloadUrl } }",
        {"id": job["id"]},
    )
    found = status["data"]["export"]
    assert found["status"] == "done" and found["filename"].endswith(".xlsx")
    assert found["contentPath"] == f"/exports/{job['id']}/content"
    assert found["downloadUrl"].startswith("memory://")  # the in-memory store signs URLs
    missing = await graphql.execute('{ export(id: "nope") { id } }')
    assert missing["errors"][0]["extensions"]["code"] == "RE-E102"


def test_no_exports_without_a_blob_store():
    graphql, _ = build(blobs=False)
    assert "export" not in graphql.schema.query_type.fields
    assert "exportNotes" not in graphql.schema.mutation_type.fields
