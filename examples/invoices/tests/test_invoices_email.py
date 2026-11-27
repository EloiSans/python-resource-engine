"""The approval email: requested by the approve transition, stored with it in
the outbox, sent by the worker's feed in Catalan; through SMTP into Mailpit."""

from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from invoices.bootstrap import registry
from invoices.notifications import build_mailer, smtp_sender
from resource_engine import ResourceManager
from resource_engine.messaging import MessageProcessor, OutboxDelivery
from resource_engine.processes import ProcessFeed, ProcessRunner
from resource_engine.testing import InMemoryBlobStore, InMemoryStore, RecordingEmailSender


class _Silent:
    async def send(self, message: str) -> None:
        pass


def world(sender):
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        event_delivery=OutboxDelivery(),
    )
    router = ProcessRunner(manager).router().include(build_mailer(sender).router())
    processor = MessageProcessor(router, name="invoice-processes", manager=manager)
    return manager, ProcessFeed(store.unit_of_work, processor)


async def approve(manager, email: str | None) -> int:
    customer = await manager.create(
        "customers", {"name": "ACME", "tax_id": f"B{uuid.uuid4().hex[:8]}", "email": email}
    )
    invoice = await manager.create(
        "invoices", {"customer": "ACME", "amount": "120.50", "customer_id": customer.id}
    )
    await manager.transition("invoices", invoice.id, "submit")
    await manager.documents.attach(
        "invoices", invoice.id, "signed_pdf", b"%PDF-1.7",
        filename="signada.pdf", content_type="application/pdf",
    )  # fmt: skip
    await manager.transition("invoices", invoice.id, "approve")
    return invoice.id


async def test_approving_an_invoice_emails_its_customer_in_catalan():
    sender = RecordingEmailSender()
    manager, feed = world(sender)
    number = await approve(manager, "compres@acme.example")
    assert sender.sent == []  # only once the worker runs
    await feed.run_once()
    [message] = sender.to("compres@acme.example")
    assert message.subject == f"Factura {number} aprovada"
    assert f"La factura {number}, de 120.50 €, ha estat aprovada." in message.text
    assert '<html lang="ca">' in message.html and "<strong>120.50 €</strong>" in message.html
    assert message.sender == "Factures <factures@invoices.example>"


async def test_customers_without_an_address_get_nothing():
    sender = RecordingEmailSender()
    manager, feed = world(sender)
    await approve(manager, None)
    await feed.run_once()
    assert sender.sent == []


def _mailpit() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 1026), timeout=1):
            return True
    except OSError:
        return False


def mailpit(test):
    """The emulator marker, and a skip the emulator CI job turns into a failure."""
    test = pytest.mark.emulator("mailpit")(test)
    if not _mailpit():
        test = pytest.mark.skip(reason="Mailpit not reachable on 127.0.0.1:1026")(test)
    return test


@mailpit
async def test_the_email_reaches_mailpit():
    address = f"{uuid.uuid4().hex[:8]}@acme.example"
    manager, feed = world(smtp_sender())
    number = await approve(manager, address)
    await feed.run_once()
    async with httpx.AsyncClient() as http:
        found = (
            await http.get("http://127.0.0.1:8026/api/v1/search", params={"query": f"to:{address}"})
        ).json()
    [message] = found["messages"]
    assert message["Subject"] == f"Factura {number} aprovada"
