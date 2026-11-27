"""Suppressed recipients are left out; bounces and complaints suppress."""

from __future__ import annotations

from resource_engine.email import EmailEventHandler, InMemorySuppressionList, Mailer
from resource_engine.ports.email import EmailEvent
from resource_engine.templates import StringTemplates
from resource_engine.testing import RecordingEmailSender

TEMPLATES = StringTemplates({"email/hi.subject.txt": "Hi", "email/hi.txt": "Hi $name"})


async def test_bounces_and_complaints_stop_later_emails():
    suppressions = InMemorySuppressionList()
    sender = RecordingEmailSender()
    mailer = Mailer(sender, TEMPLATES, from_address="a@example.com", suppressions=suppressions)
    handler = EmailEventHandler(suppressions)
    suppressed = await handler.handle(
        [
            EmailEvent("bounce", "Gone@Example.com", permanent=True, provider="test"),
            EmailEvent("bounce", "full@example.com", permanent=False),  # soft: kept
            EmailEvent("complaint", "angry@example.com", permanent=True),
            EmailEvent("delivery", "ok@example.com"),
        ]
    )
    assert suppressed == 2
    await mailer.send("hi", ["Ann <gone@example.com>", "full@example.com"], {"name": "x"},
                      bcc=["angry@example.com"])  # fmt: skip
    [message] = sender.sent
    assert message.to == ("full@example.com",) and message.bcc == ()
    assert await mailer.send("hi", ["gone@example.com"], {"name": "x"}) is None  # nobody left
    assert len(sender.sent) == 1
    await suppressions.release("gone@example.com")
    await mailer.send("hi", ["gone@example.com"], {"name": "x"})
    assert len(sender.sent) == 2
