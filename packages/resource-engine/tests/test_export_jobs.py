"""Background exports (``ExportJobs``), printouts and documents attached to
requested emails."""

from __future__ import annotations

import dataclasses
import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict
from resource_engine import (
    Action,
    DocumentSlot,
    ListQuery,
    OperationContext,
    Resource,
    ResourceManager,
    ResourceRegistry,
)
from resource_engine.authz import (
    AuthzConfig,
    IdpRoleSource,
    PolicyAuthorizer,
    SystemRole,
    system_principal,
)
from resource_engine.email import EmailRequested, Mailer
from resource_engine.errors import (
    ConfigurationError,
    DocumentNotAvailable,
    EntityNotFound,
    InvalidQuery,
    MessageRejected,
    OperationNotEnabled,
    PermissionDenied,
)
from resource_engine.exports import ExportJobs, dump_actor, load_actor
from resource_engine.generation import DocumentGenerator, Printout
from resource_engine.messaging.envelope import EventEnvelope, to_jsonable
from resource_engine.ports.identity import Principal
from resource_engine.templates import StringTemplates
from resource_engine.testing import (
    InMemoryBlobStore,
    InMemoryStore,
    RecordingEmailSender,
    RecordingEventPublisher,
)


@dataclass
class Bill:
    customer: str
    amount: Decimal
    secret: str = "s"
    id: int | None = None


class BillIn(BaseModel):
    customer: str
    amount: Decimal
    secret: str = "s"


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


templates = StringTemplates(
    {
        "documents/bill.html": "<h1>$customer</h1><p>$amount $secret</p>",
        "documents/bill.txt": "Bill for $customer",
        "email/bill_sent.subject.txt": "Your bill",
        "email/bill_sent.txt": "Attached.",
    }
)
generator = DocumentGenerator(templates)


def bill_context(record, actor):  # noqa: ANN001, ANN202
    return {"secret": "-", **record}  # the secret only for those who may read it


class Print(Action):
    """Prints the bill into its slot."""

    async def execute(self, ctx: OperationContext) -> None:
        bill = ctx.entity
        doc = await generator.attach(
            ctx,
            "printed",
            "bill",
            format="txt",
            context={"customer": bill.customer},
        )
        ctx.record_event(EmailRequested(template="bill_sent", to=["a@example.com"],
                                        documents=[doc.id]))  # fmt: skip


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "read": BillOut}
    operations = ["list", "get", "create"]
    filterable = ["customer"]
    sortable = ["id", "amount"]
    restricted_fields = ["secret"]
    actions = [Print]
    documents = [DocumentSlot("printed", upload="api")]
    printouts = {"bill": Printout(formats=("html", "txt"), context=bill_context)}


READER = Principal(subject="r", issuer="i", roles=frozenset({"readers"}), tenant="t1")
OTHER = Principal(subject="o", issuer="i", roles=frozenset({"readers"}))
NOBODY = Principal(subject="n", issuer="i")
SYSTEM = system_principal("tests")


@pytest.fixture
async def manager():
    registry = ResourceRegistry()
    registry.register(BillResource)
    registry.register_authz()
    registry.register_documents()
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        role_source=IdpRoleSource({"readers": "reader"}),
        system_roles=[SystemRole("reader", grants=["bills:list", "bills:get"])],
    )
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(config, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        generator=generator,
        event_publisher=RecordingEventPublisher(),
    )
    for customer, amount in [("ACME", "10.50"), ("Globex", "7"), ("ACME", "3")]:
        await manager.create("bills", {"customer": customer, "amount": amount}, actor=SYSTEM)
    return manager


async def content(manager, job_id, actor):  # noqa: ANN001, ANN201
    _, stream = await manager.exports.open(job_id, actor=actor)
    return b"".join([chunk async for chunk in stream])


# ------------------------------------------------------------- exports


async def test_a_background_export_is_written_by_the_worker_as_the_requester(manager):
    exports = manager.exports
    job = await exports.request(
        "bills", ListQuery(filters={"customer": "ACME"}, order_by=["-amount"]), actor=READER
    )
    assert job.status == "pending" and job.rows == 2
    with pytest.raises(DocumentNotAvailable):
        await exports.open(job.id, actor=READER)
    assert await exports.run_once() == 1
    done = await exports.get(job.id, actor=READER)
    assert done.status == "done" and done.filename.endswith(".csv") and done.size
    lines = (await content(manager, job.id, READER)).decode("utf-8-sig").splitlines()
    assert lines == ["customer,amount,secret,id", "ACME,10.50,,1", "ACME,3,,3"]  # secret hidden
    assert await exports.run_once() == 0  # nothing left to do


async def test_only_the_requester_sees_an_export(manager):
    job = await manager.exports.request("bills", actor=READER)
    for actor in (OTHER, None):
        with pytest.raises(EntityNotFound):
            await manager.exports.get(job.id, actor=actor)
    with pytest.raises(EntityNotFound):
        await manager.exports.get("../../etc", actor=READER)


async def test_the_request_checks_the_permission_and_the_query(manager):
    with pytest.raises(PermissionDenied):
        await manager.exports.request("bills", actor=NOBODY)
    with pytest.raises(InvalidQuery):
        await manager.exports.request("bills", format="pdf", actor=READER)  # type: ignore[arg-type]
    small = ExportJobs(manager, manager.exports.store, max_rows=2)
    with pytest.raises(InvalidQuery) as caught:
        await small.request("bills", actor=READER)
    assert caught.value.details == {"total": 3, "max_rows": 2}


async def test_xlsx_and_a_failure(manager):
    exports = manager.exports
    xlsx = await exports.request("bills", format="xlsx", actor=SYSTEM)
    with pytest.raises(InvalidQuery):  # checked when requested
        await exports.request("bills", ListQuery(order_by=["secret"]), actor=SYSTEM)
    broken = await exports.request("bills", actor=SYSTEM)
    broken.order_by = ["secret"]  # the resource changed before the worker ran
    await exports._save(broken)
    await exports.run_once()
    rows = list(
        load_workbook(io.BytesIO(await content(manager, xlsx.id, SYSTEM)))
        .active.iter_rows(values_only=True)
    )  # fmt: skip
    assert rows[0] == ("customer", "amount", "secret", "id") and len(rows) == 4
    failed = await exports.get(broken.id, actor=SYSTEM)
    assert failed.status == "failed" and failed.error["code"] == "RE-E103"


async def test_expired_exports_are_deleted_and_stale_ones_run_again(manager):
    exports = ExportJobs(manager, manager.exports.store, retention=timedelta(0))
    job = await exports.request("bills", actor=READER)
    await exports.run(job)  # done, expires at once
    await exports.run_once()
    assert not [b async for b in exports.store.list("exports/")]

    stale = ExportJobs(manager, manager.exports.store, stale_after=timedelta(0))
    job = await stale.request("bills", actor=READER)
    job.status = "running"
    job.started_at = job.created_at
    await stale._save(job)  # its worker died
    assert await stale.run_once() == 1
    assert (await stale.get(job.id, actor=READER)).status == "done"


def test_actors_round_trip():
    assert load_actor(dump_actor(READER)) == READER
    assert load_actor(dump_actor(SYSTEM)) == SYSTEM
    assert load_actor(dump_actor("alice")) == "alice" and load_actor(dump_actor(None)) is None
    with pytest.raises(ConfigurationError):
        dump_actor(object())


async def test_without_a_blob_store_there_are_no_background_exports():
    registry = ResourceRegistry()
    registry.register(BillResource)
    with pytest.raises(ConfigurationError):  # printouts need a generator
        ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)


# ----------------------------------------------------------- printouts


async def test_a_printout_renders_the_record_as_the_caller(manager):
    document, filename = await manager.generator.printout(manager, "bills", 1, "bill", actor=READER)
    assert filename == "bill-1.html" and document.content_type.startswith("text/html")
    assert document.content == b"<h1>ACME</h1><p>10.50 -</p>"  # the secret is hidden
    full, _ = await manager.generator.printout(manager, "bills", 1, "bill", actor=SYSTEM)
    assert full.content == b"<h1>ACME</h1><p>10.50 s</p>"
    text, name = await manager.generator.printout(
        manager, "bills", 2, "bill", format="txt", actor=READER
    )
    assert text.content == b"Bill for Globex" and name == "bill-2.txt"
    with pytest.raises(PermissionDenied):
        await manager.generator.printout(manager, "bills", 1, "bill", actor=NOBODY)
    with pytest.raises(OperationNotEnabled):
        await manager.generator.printout(manager, "bills", 1, "bill", format="pdf", actor=READER)
    with pytest.raises(OperationNotEnabled):
        await manager.generator.printout(manager, "bills", 1, "receipt", actor=READER)


# ------------------------------------------------------ email attachments


async def test_requested_emails_attach_the_documents_they_name(manager):
    await manager.run_action("bills", "print", 1, actor=SYSTEM)
    [event] = [e for e in manager.delivery.publisher.events if isinstance(e, EmailRequested)]
    envelope = EventEnvelope(
        type="EmailRequested",
        payload=to_jsonable(dataclasses.asdict(event)),
        occurred_at=datetime.now(UTC),
    )
    sender = RecordingEmailSender()
    mailer = Mailer(sender, templates, from_address="bills@example.com",
                    documents=manager.documents)  # fmt: skip
    await mailer.handle(envelope)
    [message] = sender.sent
    [attachment] = message.attachments
    assert attachment.filename == "bill-1.txt" and attachment.content == b"Bill for ACME"

    tiny = Mailer(sender, templates, from_address="bills@example.com",
                  documents=manager.documents, max_attachment_bytes=3)  # fmt: skip
    with pytest.raises(MessageRejected):
        await tiny.document_attachments(list(event.documents))
    with pytest.raises(MessageRejected):
        await mailer.document_attachments(["missing"])
    with pytest.raises(ConfigurationError):
        await Mailer(sender, templates, from_address="b@example.com").document_attachments(["x"])
