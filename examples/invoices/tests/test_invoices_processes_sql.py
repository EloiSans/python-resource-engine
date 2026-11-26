"""The invoice payment process over SQLAlchemy (a SQLite file), driven by the
example's worker: the outbox feeds the process, exactly once."""

from __future__ import annotations

from datetime import UTC, datetime

from invoices.app import build_manager
from invoices.authz import seed
from invoices.infrastructure.persistence import map_entities, metadata
from invoices.worker import build
from resource_engine import ListQuery
from resource_engine.authz import system_principal
from resource_engine.messaging import EventEnvelope, MessageProcessor
from resource_engine.ports.identity import Principal
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine.processes import ProcessRunner
from resource_engine.testing import InMemoryBlobStore
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ELI = Principal(
    subject="eli", issuer="https://sso", roles=frozenset({"issuer", "approver"}), tenant="acme"
)


async def test_payment_process(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'invoices.db'}"
    map_entities()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    manager = build_manager(sessions, blob_store=InMemoryBlobStore())
    await seed(manager, system_principal("seed"))

    invoice = await manager.create("invoices", {"customer": "ACME", "amount": "80"}, actor=ELI)
    await manager.transition("invoices", invoice.id, "submit", actor=ELI)
    await manager.documents.attach(
        "invoices", invoice.id, "signed_pdf", b"%PDF-1.7", filename="s.pdf",
        content_type="application/pdf", actor=ELI,
    )  # fmt: skip
    await manager.transition("invoices", invoice.id, "approve", actor=ELI)

    runner = build(url)
    assert {r.job: r.outcome for r in await runner.run_once()} == {
        "processes": "ok",
        "timers": "ok",
    }
    [payment] = (await manager.list("invoice_payments", ListQuery(), actor=ELI)).items
    assert (payment.status, payment.organization) == ("awaiting", "acme")

    processor = MessageProcessor(
        ProcessRunner(manager).router(), name="invoice-processes", manager=manager
    )
    envelope = EventEnvelope(
        type="PaymentReceived",
        payload={"invoice_id": invoice.id, "amount": "80"},
        occurred_at=datetime.now(UTC),
    )
    first = await processor.process(ReceivedMessage(envelope, message_id=envelope.id))
    again = await processor.process(ReceivedMessage(envelope, message_id=envelope.id))
    assert (first.outcome, again.outcome) == ("ack", "duplicate")
    assert (await manager.get("invoices", invoice.id, actor=ELI)).status == "paid"
    assert (await manager.get("invoice_payments", payment.id, actor=ELI)).status == "settled"
    await engine.dispose()
