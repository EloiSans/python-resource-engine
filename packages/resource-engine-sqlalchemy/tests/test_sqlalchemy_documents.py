from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, Resource, ResourceManager, ResourceRegistry
from resource_engine.documents import DocumentJanitor
from resource_engine.documents.content import read_all
from resource_engine.testing import InMemoryBlobStore
from resource_engine_sqlalchemy import documents_table, map_documents, unit_of_work_factory
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import registry as orm_registry


@dataclass
class Folder:
    name: str
    id: int | None = None


class FolderIn(BaseModel):
    name: str


class FolderOut(FolderIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class FolderResource(Resource):
    model = Folder
    schemas = {"create": FolderIn, "read": FolderOut}
    operations = ["get", "create", "delete"]
    documents = [DocumentSlot("files", multiple=True), DocumentSlot("cover")]


metadata = MetaData()
folders = Table(
    "folders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(50)),
)
mapper = orm_registry(metadata=metadata)
mapper.map_imperatively(Folder, folders)
map_documents(mapper, documents_table(metadata))


@pytest.fixture
async def manager(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'docs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    registry = ResourceRegistry()
    registry.register(FolderResource)
    registry.register_documents()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield ResourceManager(
        registry, unit_of_work_factory(sessions, registry), blob_store=InMemoryBlobStore()
    )
    await engine.dispose()


async def test_documents_on_sqlalchemy(manager):
    folder = await manager.create("folders", {"name": "Contractes"})
    docs = manager.documents
    a = await docs.attach(
        "folders", folder.id, "files", b"a", filename="a.txt", content_type="text/plain"
    )
    await docs.attach(
        "folders", folder.id, "files", b"b", filename="b.txt", content_type="text/plain"
    )
    c1 = await docs.attach(
        "folders", folder.id, "cover", b"1", filename="1.png", content_type="image/png"
    )
    c2 = await docs.attach(
        "folders", folder.id, "cover", b"2", filename="2.png", content_type="image/png"
    )
    assert [d.filename for d in await docs.list("folders", folder.id)] == [
        "a.txt",
        "b.txt",
        "2.png",
    ]
    assert (await docs.get(c1.id)).status == "deleted"
    _, stream = await docs.open(c2.id)
    assert await read_all(stream) == b"2"

    await docs.detach("folders", folder.id, a.id)
    await manager.delete("folders", folder.id)
    assert docs.store.blobs == {}
    stats = await DocumentJanitor(docs).run_once()
    assert stats.purged == 0 and stats.orphans == 0
