"""Email from templates: composing, locale fallback, escaping, and requests
recorded in a transaction and sent from the outbox exactly once per request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import OperationContext, Resource, ResourceManager, ResourceRegistry
from resource_engine.email import EmailRequested, Mailer, attachment, to_mime
from resource_engine.errors import ConfigurationError, TemplateFailed, TemplateNotFound
from resource_engine.messaging import EventEnvelope, EventRouter, MessageProcessor, OutboxDelivery
from resource_engine.processes import ProcessFeed
from resource_engine.templates import StringTemplates, locale_candidates, split_locale
from resource_engine.testing import InMemoryStore, RecordingEmailSender

TEMPLATES = {
    "email/welcome.subject.txt": "Welcome,\n  $name",
    "email/welcome.txt": "Hello $name, you owe $amount.",
    "email/welcome.html": "<p>Hello <b>$name</b> from $app</p>",
    "ca/email/welcome.subject.txt": "Benvinguda, $name",
    "ca/email/welcome.txt": "Hola $name, deus $amount.",
    "email/text_only.subject.txt": "Only text",
    "email/text_only.txt": "Plain $name",
    "email/no_body.subject.txt": "Nothing",
}


def mailer(sender=None, **kw) -> Mailer:
    return Mailer(
        sender or RecordingEmailSender(),
        StringTemplates(TEMPLATES),
        from_address="Factures <factures@example.com>",
        globals={"app": "Factures"},
        **kw,
    )


def test_locale_candidates_and_locale_folders():
    assert locale_candidates("ca-ES", "en") == ["ca-ES", "ca", "en", None]
    assert locale_candidates("pt_BR") == ["pt-BR", "pt", None]
    assert locale_candidates(None) == [None]
    assert split_locale("ca/email/x.txt") == ("ca", "email/x.txt")
    assert split_locale("es-419/email/x.txt") == ("es-419", "email/x.txt")
    assert split_locale("email/x.txt") == (None, "email/x.txt")
    assert StringTemplates(TEMPLATES).names()[0] == "email/no_body.subject.txt"


def test_string_templates_escape_html_and_refuse_missing_values():
    templates = StringTemplates(TEMPLATES)
    html = templates.render("email/welcome.html", {"name": "<Ada>", "app": "A&B"})
    assert html == "<p>Hello <b>&lt;Ada&gt;</b> from A&amp;B</p>"
    assert templates.render("email/welcome.txt", {"name": "<Ada>", "amount": 1}).startswith(
        "Hello <Ada>"
    )
    with pytest.raises(TemplateFailed):
        templates.render("email/welcome.txt", {"name": "Ada"})
    with pytest.raises(TemplateNotFound) as info:
        templates.render("email/nope.txt", {}, locale="ca")
    assert info.value.details["tried"] == ["ca/email/nope.txt", "email/nope.txt"]


def test_compose_picks_the_locale_and_falls_back():
    m = mailer()
    catalan = m.compose("welcome", "ada@example.com", {"name": "Ada", "amount": 3}, locale="ca-ES")
    assert catalan.subject == "Benvinguda, Ada"
    assert catalan.text == "Hola Ada, deus 3."
    assert catalan.html == "<p>Hello <b>Ada</b> from Factures</p>"  # no Catalan html: fallback
    english = m.compose("welcome", ["ada@example.com"], {"name": "Ada", "amount": 3})
    assert english.subject == "Welcome, Ada"  # one line
    assert m.compose("text_only", "a@x.com", {"name": "A"}).html is None
    with pytest.raises(TemplateNotFound):
        m.compose("no_body", "a@x.com", {})
    with pytest.raises(ConfigurationError):
        m.compose("welcome", [], {"name": "A", "amount": 1})
    with pytest.raises(ConfigurationError):
        Mailer(RecordingEmailSender(), StringTemplates({}), from_address="nobody")


def test_mime_document():
    message = mailer().compose(
        "welcome",
        ["ada@example.com"],
        {"name": "Ada", "amount": 3},
        cc=["cc@example.com"],
        bcc=["hidden@example.com"],
        attachments=[attachment("factura.pdf", b"%PDF-1.7")],
        message_id="<1@example.com>",
    )
    mime = to_mime(message)
    assert mime["To"] == "ada@example.com" and mime["Cc"] == "cc@example.com"
    assert mime["Bcc"] is None and "hidden@example.com" in message.recipients
    assert mime["Message-ID"] == "<1@example.com>"
    parts = [p.get_content_type() for p in mime.walk()]
    assert {"text/plain", "text/html", "application/pdf"} <= set(parts)


async def test_router_include_merges_handlers():
    seen = []
    first = EventRouter(ignore_unknown=True)
    first.register("A", lambda e: seen.append("a"))
    second = EventRouter()
    second.register("B", lambda e: seen.append("b"))
    first.include(second)
    for kind in ("A", "B", "C"):
        await first.dispatch(EventEnvelope(type=kind, payload={}, occurred_at=datetime.now(UTC)))
    assert seen == ["a", "b"]


# ------------------------------------------------- requested in a transaction


@dataclass
class Member:
    name: str
    email: str
    id: int | None = None


class MemberIn(BaseModel):
    name: str
    email: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


def welcome(ctx: OperationContext) -> None:
    """Welcomes the new member."""
    if ctx.entity.name == "Boom":
        raise ValueError("refused after recording the email")
    ctx.record_event(
        EmailRequested(
            template="welcome",
            to=[ctx.entity.email],
            context={"name": ctx.entity.name, "amount": Decimal("12.50"), "since": date.today()},
            locale="ca",
            resource="members",
            entity_id=ctx.entity.id,
        )
    )


class MemberResource(Resource):
    """Members."""

    name = "members"
    model = Member
    schemas = {"create": MemberIn, "read": MemberOut}
    operations = ["list", "get", "create"]
    hooks = {"after_create": [welcome]}


@pytest.fixture
def world():
    registry = ResourceRegistry()
    registry.register(MemberResource)
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, event_delivery=OutboxDelivery())
    sender = RecordingEmailSender()
    processor = MessageProcessor(mailer(sender).router(), name="emails", manager=manager)
    return manager, sender, ProcessFeed(store.unit_of_work, processor), processor


async def test_requested_emails_go_out_after_the_commit_once(world):
    manager, sender, feed, processor = world
    member = await manager.create("members", {"name": "Ada", "email": "ada@example.com"})
    assert sender.sent == []  # recorded with the change, not sent yet
    await feed.run_once()
    [message] = sender.to("ada@example.com")
    assert message.subject == "Benvinguda, Ada" and message.text == "Hola Ada, deus 12.50."
    assert message.message_id.endswith("@example.com>")
    assert member.id is not None
    await feed.run_once()  # nothing pending
    assert len(sender.sent) == 1

    with pytest.raises(ValueError):
        await manager.create("members", {"name": "Boom", "email": "boom@example.com"})
    await feed.run_once()
    assert sender.to("boom@example.com") == []  # rolled back with the change


async def test_failures_retry_and_rejections_dead_letter(world):
    manager, sender, feed, processor = world
    sender.failures = 1
    await manager.create("members", {"name": "Ada", "email": "ada@example.com"})
    await feed.run_once()
    assert sender.sent == []  # mail service down: retried on a later run
    await feed.run_once()
    assert len(sender.to("ada@example.com")) == 1

    sender.reject = {"bad@example.com"}
    await manager.create("members", {"name": "Bad", "email": "bad@example.com"})
    await feed.run_once()
    await feed.run_once()
    assert sender.to("bad@example.com") == []  # permanent: settled, never retried


async def test_a_redelivered_request_is_not_sent_twice(world):
    from resource_engine.ports.messaging import ReceivedMessage

    manager, sender, feed, processor = world
    envelope = EventEnvelope(
        type="EmailRequested",
        payload={"template": "text_only", "to": ["a@example.com"], "context": {"name": "A"}},
        occurred_at=datetime.now(UTC),
    )
    for _ in range(2):
        await processor.process(ReceivedMessage(envelope, message_id=envelope.id))
    assert len(sender.sent) == 1
