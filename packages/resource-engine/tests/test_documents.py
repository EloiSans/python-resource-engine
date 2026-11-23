from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, Resource, ResourceManager, ResourceRegistry
from resource_engine.documents import (
    DocumentJanitor,
    DocumentPipeline,
    NewDocument,
    ProcessingResult,
)
from resource_engine.documents.content import read_all
from resource_engine.errors import (
    ConfigurationError,
    ContentTypeNotAllowed,
    DocumentNotAvailable,
    DocumentSlotNotFound,
    DocumentTooLarge,
    OperationNotEnabled,
    RequiredDocumentMissing,
    UploadNotVerified,
)
from resource_engine.messaging import EventRouter, EventSerializer
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingEventPublisher
from statemachine import State, StateMachine

PDF = b"%PDF-1.7 signed"


@dataclass
class Contract:
    title: str
    status: str | None = None
    id: int | None = None


class ContractMachine(StateMachine):
    draft = State(initial=True)
    signed = State(final=True)
    sign = draft.to(signed)


class ContractIn(BaseModel):
    title: str


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    status: str


calls: list[str] = []
fail_after_attach = {"on": False}


def after_attach(ctx):
    calls.append(f"after {ctx.slot}")
    if fail_after_attach["on"]:
        raise RuntimeError("hook failed after the blob was stored")


class ContractResource(Resource):
    model = Contract
    schemas = {"create": ContractIn, "read": ContractOut}
    operations = ["get", "create", "delete"]
    state_machine = ContractMachine
    documents = [
        DocumentSlot(
            "signed", content_types=["application/pdf"], max_bytes=1000, required_for=["sign"]
        ),
        DocumentSlot("annexes", multiple=True, upload="presigned", max_bytes=100),
        DocumentSlot("scans", content_types=["image/*"], multiple=True, processing=True),
        DocumentSlot("thumbnails", content_types=["image/png"], multiple=True, upload="api"),
    ]
    hooks = {
        "before_attach:signed": lambda ctx: calls.append(f"before {ctx.slot}"),
        "after_attach": after_attach,
        "before_detach": lambda ctx: calls.append("before detach"),
    }


@pytest.fixture
def env():
    registry = ResourceRegistry()
    registry.register(ContractResource)
    registry.register_documents()
    store = InMemoryStore.from_registry(registry)
    blobs = InMemoryBlobStore()
    events = RecordingEventPublisher()
    manager = ResourceManager(
        registry, store.unit_of_work, blob_store=blobs, event_publisher=events
    )
    calls.clear()
    return manager, blobs, events


async def new_contract(manager) -> int:
    return (await manager.create("contracts", {"title": "Lloguer"})).id


async def test_attach_download_and_replace_single_slot(env):
    manager, blobs, events = env
    cid = await new_contract(manager)
    first = await manager.documents.attach(
        "contracts",
        cid,
        "signed",
        PDF,
        filename="../../etc/sig?.pdf",
        content_type="application/pdf",
    )
    assert first.status == "available" and first.size == len(PDF) and first.filename == "sig_.pdf"
    assert calls == ["before signed", "after signed"]
    _, stream = await manager.documents.open(first.id)
    assert await read_all(stream) == PDF

    second = await manager.documents.attach(
        "contracts", cid, "signed", b"%PDF v2", filename="v2.pdf", content_type="application/pdf"
    )
    docs = await manager.documents.list("contracts", cid)
    assert [d.id for d in docs] == [second.id]  # the first was replaced...
    assert (await manager.documents.get(first.id)).status == "deleted"
    assert len(blobs.blobs) == 1  # ...and its blob purged after commit
    assert "DocumentDetached" in events.names() and events.names().count("DocumentAttached") == 2


async def test_slot_rules(env):
    manager, _, _ = env
    cid = await new_contract(manager)
    docs = manager.documents
    with pytest.raises(ContentTypeNotAllowed):
        await docs.attach(
            "contracts", cid, "signed", b"x", filename="a.txt", content_type="text/plain"
        )
    with pytest.raises(DocumentTooLarge):
        await docs.attach(
            "contracts",
            cid,
            "signed",
            b"x" * 2000,
            filename="a.pdf",
            content_type="application/pdf",
        )
    with pytest.raises(DocumentSlotNotFound):
        await docs.attach("contracts", cid, "nope", b"x", filename="a", content_type="a/b")
    with pytest.raises(OperationNotEnabled):
        await docs.attach("contracts", cid, "annexes", b"x", filename="a", content_type="a/b")


async def test_rollback_deletes_the_staged_blob(env):
    manager, blobs, _ = env
    cid = await new_contract(manager)
    fail_after_attach["on"] = True
    try:
        with pytest.raises(RuntimeError):
            await manager.documents.attach(
                "contracts", cid, "signed", PDF, filename="s.pdf", content_type="application/pdf"
            )
    finally:
        fail_after_attach["on"] = False
    assert blobs.blobs == {}
    assert await manager.documents.list("contracts", cid) == []


async def test_required_document_gates_the_transition(env):
    manager, _, _ = env
    cid = await new_contract(manager)
    with pytest.raises(RequiredDocumentMissing):
        await manager.transition("contracts", cid, "sign")
    await manager.documents.attach(
        "contracts", cid, "signed", PDF, filename="s.pdf", content_type="application/pdf"
    )
    assert (await manager.transition("contracts", cid, "sign")).status == "signed"


async def test_presigned_upload_flow(env):
    manager, blobs, _ = env
    cid = await new_contract(manager)
    docs = manager.documents
    ticket = await docs.request_upload(
        "contracts", cid, "annexes", filename="annex.txt", content_type="text/plain"
    )
    assert ticket.document.status == "pending" and ticket.upload.method == "PUT"
    with pytest.raises(UploadNotVerified):  # nothing uploaded yet
        await docs.confirm_upload(ticket.document.id)
    with pytest.raises(DocumentNotAvailable):
        await docs.open(ticket.document.id)
    await blobs.upload(ticket.upload, b"annex body")
    confirmed = await docs.confirm_upload(ticket.document.id)
    assert confirmed.status == "available" and confirmed.size == 10
    assert (await docs.confirm_upload(ticket.document.id)).status == "available"  # idempotent
    url = await docs.download_url(confirmed.id)
    assert await blobs.download(url) == b"annex body"

    too_big = await docs.request_upload(
        "contracts", cid, "annexes", filename="big.txt", content_type="text/plain"
    )
    await blobs.upload(too_big.upload, b"x" * 500)
    with pytest.raises(UploadNotVerified):
        await docs.confirm_upload(too_big.document.id)
    assert await blobs.head(f"contracts/{cid}/{too_big.document.id}") is None


async def test_deleting_the_owner_cascades(env):
    manager, blobs, _ = env
    cid = await new_contract(manager)
    await manager.documents.attach(
        "contracts", cid, "thumbnails", b"\x89PNG", filename="t.png", content_type="image/png"
    )
    await manager.delete("contracts", cid)
    assert blobs.blobs == {}


async def test_detach_runs_hooks_and_purges(env):
    manager, blobs, _ = env
    cid = await new_contract(manager)
    doc = await manager.documents.attach(
        "contracts", cid, "thumbnails", b"\x89PNG", filename="t.png", content_type="image/png"
    )
    await manager.documents.detach("contracts", cid, doc.id)
    assert "before detach" in calls and blobs.blobs == {}
    assert await manager.documents.list("contracts", cid) == []


class Ocr:
    name = "ocr"

    def accepts(self, document):
        return document.content_type.startswith("image/")

    async def process(self, document, content):
        data = await read_all(content())
        if data.startswith(b"VIRUS"):
            return ProcessingResult(reject="infected")
        thumb = NewDocument("thumbnails", b"\x89PNG small", "thumb.png", "image/png")
        return ProcessingResult(metadata={"text": data.decode()}, derived=[thumb])


async def test_pipeline_completes_rejects_and_derives(env):
    manager, _, events = env
    cid = await new_contract(manager)
    router = DocumentPipeline(manager.documents, [Ocr()]).register(EventRouter(ignore_unknown=True))
    good = await manager.documents.attach(
        "contracts", cid, "scans", b"hello", filename="s.jpg", content_type="image/jpeg"
    )
    bad = await manager.documents.attach(
        "contracts", cid, "scans", b"VIRUS", filename="v.jpg", content_type="image/jpeg"
    )
    assert good.status == "processing"
    with pytest.raises(DocumentNotAvailable):
        await manager.documents.open(good.id)

    serializer = EventSerializer()
    for event in list(events.events):
        await router.dispatch(serializer.to_envelope(event))
        await router.dispatch(serializer.to_envelope(event))  # redelivery is harmless

    good, bad = await manager.documents.get(good.id), await manager.documents.get(bad.id)
    assert good.status == "available" and good.metadata == {"ocr": {"text": "hello"}}
    assert bad.status == "rejected" and bad.error == "ocr: infected"
    thumbs = await manager.documents.list("contracts", cid, "thumbnails")
    assert len(thumbs) == 1 and thumbs[0].metadata["derived_from"] == good.id


async def test_ctx_documents_inside_an_action(env):
    manager, _, _ = env
    cid = await new_contract(manager)
    from resource_engine.application.context import OperationContext

    ctx: OperationContext = manager._context(ContractResource.definition(), "action", entity_id=cid)
    async with manager._transaction(ctx) as uow:
        await ctx.documents.attach(NewDocument("thumbnails", b"\x89PNG", "gen.png", "image/png"))
        await uow.commit()
    assert len(await manager.documents.list("contracts", cid, "thumbnails")) == 1


async def test_janitor_expires_purges_and_removes_orphans(env):
    manager, blobs, _ = env
    cid = await new_contract(manager)
    docs = manager.documents
    ticket = await docs.request_upload(
        "contracts", cid, "annexes", filename="late.txt", content_type="text/plain"
    )
    await blobs.put("contracts/1/orphan", b"left behind by a crash", content_type="text/plain")
    later = datetime.now(UTC) + timedelta(hours=2)
    stats = await DocumentJanitor(docs).run_once(now=later)
    assert stats.expired == 1 and stats.orphans == 1
    assert (await docs.get(ticket.document.id)).status == "deleted"
    assert "contracts/1/orphan" not in blobs.blobs


def test_configuration_errors():
    registry = ResourceRegistry()
    registry.register(ContractResource)
    with pytest.raises(ConfigurationError):  # slots without a blob store
        ResourceManager(registry, InMemoryStore().unit_of_work)
    with pytest.raises(ConfigurationError):  # blob store without the documents resource
        ResourceManager(registry, InMemoryStore().unit_of_work, blob_store=InMemoryBlobStore())

    class Bad(Resource):
        model = Contract
        documents = [DocumentSlot("x", required_for=["sign"])]

    with pytest.raises(ConfigurationError):
        Bad.definition()
