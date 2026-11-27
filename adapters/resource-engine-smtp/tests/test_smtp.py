"""SmtpEmailSender against Mailpit (compose, SMTP :1026, API :8026), and how
SMTP answers map to retried and permanent failures."""

from __future__ import annotations

import uuid

import aiosmtplib
import httpx
import pytest
from resource_engine.email import Mailer, attachment
from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage
from resource_engine.templates import StringTemplates
from resource_engine_smtp import SmtpEmailSender

API = "http://127.0.0.1:8026/api/v1"


@pytest.mark.emulator("mailpit")
async def test_a_message_reaches_mailpit_with_its_parts():
    tag = uuid.uuid4().hex[:8]
    templates = StringTemplates(
        {
            "email/hello.subject.txt": f"Hola $name {tag}",
            "email/hello.txt": "Hola $name",
            "email/hello.html": "<p>Hola <b>$name</b></p>",
        }
    )
    sender = SmtpEmailSender("127.0.0.1", 1026, tls=False)
    mailer = Mailer(sender, templates, from_address="Factures <factures@example.com>")
    message_id = await mailer.send(
        "hello",
        ["ada@example.com"],
        {"name": "Ada"},
        bcc=["hidden@example.com"],
        attachments=[attachment("factura.pdf", b"%PDF-1.7")],
        message_id=f"<{tag}@example.com>",
    )
    assert message_id == f"<{tag}@example.com>"
    async with httpx.AsyncClient() as http:
        found = (await http.get(f"{API}/search", params={"query": f"subject:{tag}"})).json()
        [summary] = found["messages"]
        detail = (await http.get(f"{API}/message/{summary['ID']}")).json()
    assert detail["MessageID"] == f"{tag}@example.com"
    assert detail["Text"].strip() == "Hola Ada" and "<b>Ada</b>" in detail["HTML"]
    assert [a["FileName"] for a in detail["Attachments"]] == ["factura.pdf"]
    assert {b["Address"] for b in detail["Bcc"]} == {"hidden@example.com"}
    assert detail["From"]["Name"] == "Factures"


async def test_an_unreachable_server_is_retried():
    sender = SmtpEmailSender("127.0.0.1", 9, tls=False, timeout=2)  # discard port: closed
    message = EmailMessage(sender="a@example.com", to=["b@example.com"], subject="x", text="x")
    with pytest.raises(RemoteCallFailed):
        await sender.send(message)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (aiosmtplib.SMTPRecipientsRefused([aiosmtplib.SMTPRecipientRefused(550, "no", "b@x.com")]),
         EmailRejected),
        (aiosmtplib.SMTPSenderRefused(553, "not yours", "a@x.com"), EmailRejected),
        (aiosmtplib.SMTPResponseException(451, "try later"), RemoteCallFailed),
        (aiosmtplib.SMTPServerDisconnected("gone"), RemoteCallFailed),
    ],
)  # fmt: skip
async def test_smtp_answers_map_to_permanent_or_retried(monkeypatch, error, expected):
    async def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(aiosmtplib, "send", fail)
    message = EmailMessage(sender="a@x.com", to=["b@x.com"], subject="x", text="x")
    with pytest.raises(expected):
        await SmtpEmailSender("smtp.example.com").send(message)


def test_tls_modes():
    assert SmtpEmailSender("h", 465).use_tls is True
    assert SmtpEmailSender("h", 587).start_tls is None  # upgrade when offered
    assert SmtpEmailSender("h", 587, starttls=True).start_tls is True
    assert SmtpEmailSender("h", 1025, tls=False).start_tls is False
