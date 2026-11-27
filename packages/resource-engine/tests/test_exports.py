"""Table export (CSV, XLSX) with the caller's permissions, and documents
generated from templates and attached to records."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
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
from resource_engine.errors import ConfigurationError, InvalidQuery, TemplateNotFound
from resource_engine.exports import export_table
from resource_engine.generation import DocumentGenerator
from resource_engine.ports.identity import Principal
from resource_engine.templates import StringTemplates
from resource_engine.testing import InMemoryBlobStore, InMemoryStore


@dataclass
class Bill:
    customer: str
    amount: Decimal
    due: date
    secret: str = "s"
    tags: list[str] | None = None
    id: int | None = None


class BillIn(BaseModel):
    customer: str
    amount: Decimal
    due: date
    secret: str = "s"
    tags: list[str] | None = None


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


generator = DocumentGenerator(
    StringTemplates(
        {
            "documents/bill.html": "<h1>$resource $id</h1><p>$customer: $amount</p>",
            "documents/bill.md": "# Bill $id\n",
        }
    ),
    pdf=None,
)


class Print(Action):
    """Prints the bill."""

    async def execute(self, ctx: OperationContext) -> None:
        bill = ctx.entity
        await generator.attach(
            ctx,
            "printed",
            "bill",
            format=ctx.data.format if ctx.data else "html",
            context={"id": bill.id, "customer": bill.customer, "amount": bill.amount},
        )


class PrintIn(BaseModel):
    format: str = "html"


Print.input_schema = PrintIn


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "read": BillOut}
    operations = ["list", "get", "create"]
    filterable = ["customer"]
    sortable = ["id", "amount"]
    restricted_fields = ["secret"]
    actions = [Print]
    documents = [DocumentSlot("printed", upload="api")]


READER = Principal(subject="r", issuer="i", roles=frozenset({"readers"}))
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
    )
    for i, (customer, amount) in enumerate([("ACME", "10.50"), ("Globex", "7"), ("ACME", "3")]):
        await manager.create(
            "bills",
            {"customer": customer, "amount": amount, "due": f"2026-0{i + 1}-15",
             "tags": ["a", "b"] if i == 0 else None},
            actor=SYSTEM,
        )  # fmt: skip
    return manager


async def test_csv_has_the_callers_rows_and_leaves_hidden_fields_empty(manager):
    content, media_type, filename = await export_table(
        manager, "bills", ListQuery(filters={"customer": "ACME"}, order_by=["-amount"]),
        actor=READER,
    )  # fmt: skip
    assert media_type.startswith("text/csv") and filename.startswith("bills-")
    text = content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == "customer,amount,due,secret,tags,id"
    assert lines[1] == 'ACME,10.50,2026-01-15,,"[""a"", ""b""]",1'  # secret is hidden
    assert lines[2] == "ACME,3,2026-03-15,,,3" and len(lines) == 3


async def test_xlsx_keeps_numbers_and_dates(manager):
    content, media_type, _ = await export_table(manager, "bills", format="xlsx", actor=SYSTEM)
    assert media_type.endswith("spreadsheetml.sheet")
    sheet = load_workbook(io.BytesIO(content)).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == ("customer", "amount", "due", "secret", "tags", "id")
    assert rows[1][1] == pytest.approx(10.5) and rows[1][2].year == 2026  # native cells
    assert rows[1][3] == "s" and len(rows) == 4  # no actor: everything


async def test_limits(manager):
    with pytest.raises(InvalidQuery):
        await export_table(manager, "bills", max_rows=2, actor=SYSTEM)
    with pytest.raises(InvalidQuery):
        await export_table(manager, "bills", format="pdf", actor=SYSTEM)  # type: ignore[arg-type]


async def test_generated_documents_attach_to_the_record(manager):
    await manager.run_action("bills", "print", 1, {"format": "html"}, actor=SYSTEM)
    docs = await manager.documents.list("bills", 1, "printed", actor=SYSTEM)
    [doc] = docs
    assert doc.filename == "bill-1.html" and doc.content_type == "text/html"
    _, stream = await manager.documents.open(doc.id, actor=SYSTEM)
    body = b"".join([chunk async for chunk in stream])
    assert body == b"<h1>bills 1</h1><p>ACME: 10.50</p>"
    with pytest.raises(ConfigurationError):  # PDF needs a converter
        await manager.run_action("bills", "print", 1, {"format": "pdf"}, actor=SYSTEM)
    with pytest.raises(TemplateNotFound):
        await generator.generate("missing", {}, format="html")


async def test_pdf_goes_through_the_converter():
    class Printer:
        async def html_to_pdf(self, html: str) -> bytes:
            return b"%PDF-" + html.encode()

    pdf = DocumentGenerator(StringTemplates({"documents/x.html": "<p>$a</p>"}), pdf=Printer())
    document = await pdf.generate("x", {"a": "hi"})
    assert document.content == b"%PDF-<p>hi</p>" and document.content_type == "application/pdf"
