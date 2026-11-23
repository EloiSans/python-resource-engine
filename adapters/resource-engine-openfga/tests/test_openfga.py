"""Against a running OpenFGA (compose.emulators.yaml)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from openfga_sdk.client.models import ClientCheckRequest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import EngineAuthorizer
from resource_engine.errors import ConditionNotSupported, EntityNotFound
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import AuthorizerContract
from resource_engine_openfga import TupleSync

pytestmark = pytest.mark.emulator("openfga")

RELATIONS = {
    "{r}:get": "owner",
    "{r}:list": "owner",
    "{r}:update": "owner",
    "{r}:delete": "owner",
    "{r}:create": "creator",
    "{r}:read:secret": "auditor",
}


def relations(resource: str) -> dict[str, str]:
    return {k.format(r=resource): v for k, v in RELATIONS.items()}


def doc_tuples(engine, resource: str):
    return lambda doc: [
        (engine.user_ref(doc.owner), "owner"),
        (f"{resource}:collection", "collection"),
    ]


class TestOpenFgaContract(AuthorizerContract):
    @pytest.fixture
    async def contract_engine(self, fga_engine):
        return await fga_engine("contract_docs", relations("contract_docs"))

    async def contract_record_created(self, engine, doc):
        await TupleSync(engine, {"contract_docs": doc_tuples(engine, "contract_docs")}).write(
            "contract_docs", doc, doc.id
        )


@dataclass
class Note:
    owner: str
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    owner: str
    text: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


def boom(ctx):
    raise RuntimeError("boom")


ANN = Principal(subject="ann", issuer="idp", roles=frozenset({"writer"}))
BOB = Principal(subject="bob", issuer="idp", roles=frozenset({"writer"}))


async def build(fga_engine, *, max_objects=1000, fail=False):
    engine = await fga_engine("notes", relations("notes"))
    engine.max_objects = max_objects
    sync = TupleSync(engine, {"notes": doc_tuples(engine, "notes")})

    class NoteResource(Resource):
        model = Note
        schemas = {"create": NoteIn, "read": NoteOut}
        operations = ["list", "get", "create", "delete"]
        hooks = sync.hooks("notes")
        if fail:
            hooks = {**hooks, "after_create": [hooks["after_create"], boom]}

    registry = ResourceRegistry()
    registry.register(NoteResource)
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, authorizer=EngineAuthorizer(engine))
    return manager, engine


async def test_tuples_follow_records(fga_engine):
    manager, engine = await build(fga_engine)
    note = await manager.create("notes", {"owner": ANN.id, "text": "hi"}, actor=ANN)
    assert (await manager.get("notes", note.id, actor=ANN)).text == "hi"
    with pytest.raises(EntityNotFound):
        await manager.get("notes", note.id, actor=BOB)
    assert [n.id for n in (await manager.list("notes", actor=ANN)).items] == [note.id]
    await manager.delete("notes", note.id, actor=ANN)
    check = ClientCheckRequest(
        user=engine.user_ref(ANN.id), relation="owner", object=f"notes:{note.id}"
    )
    assert (await engine.client.check(check)).allowed is False


async def test_rolled_back_creates_leave_no_tuples(fga_engine):
    manager, engine = await build(fga_engine, fail=True)
    with pytest.raises(RuntimeError):
        await manager.create("notes", {"owner": ANN.id, "text": "x"}, actor=ANN)
    check = ClientCheckRequest(user=engine.user_ref(ANN.id), relation="owner", object="notes:1")
    assert (await engine.client.check(check)).allowed is False


async def test_large_listings_are_refused(fga_engine):
    manager, _ = await build(fga_engine, max_objects=1)
    for text in ("a", "b"):
        await manager.create("notes", {"owner": ANN.id, "text": text}, actor=ANN)
    with pytest.raises(ConditionNotSupported):
        await manager.list("notes", actor=ANN)
