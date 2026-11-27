"""Email from templates, sent after the commit.

Inside an operation, record an ``EmailRequested`` event: it is stored with the
change (the outbox) and sent only if the transaction commits::

    ctx.record_event(
        EmailRequested(template="invoice_approved", to=[customer.email],
                       context={"number": invoice.id}, locale="ca")
    )

The worker sends it: ``Mailer.router()`` handles the requests, through a
``MessageProcessor`` with the inbox, fed from the outbox (``ProcessFeed``) or
from a broker. Outside a transaction (a job, a script) call ``Mailer.send``.

A template set ``invoice_approved`` is three files under ``email/``:
``invoice_approved.subject.txt``, ``invoice_approved.txt`` and
``invoice_approved.html`` (text or html may be missing, not both).
See docs/email.md.
"""

from __future__ import annotations

import dataclasses
import logging
import mimetypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, parseaddr
from typing import Any

from resource_engine.domain.events import DomainEvent
from resource_engine.errors import ConfigurationError, TemplateNotFound
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import EventEnvelope, to_jsonable
from resource_engine.ports.email import (
    Attachment,
    EmailEvent,
    EmailMessage,
    EmailSender,
    SuppressionList,
)
from resource_engine.ports.templates import TemplateRenderer

log = logging.getLogger("resource_engine.email")


@dataclass(frozen=True, kw_only=True)
class EmailRequested(DomainEvent):
    """An email to send once the change that asked for it is committed.

    ``context`` travels as JSON (decimals and dates become strings). It is
    stored in the outbox and, with a broker, published like other events:
    keep it to what the template needs.
    """

    template: str
    to: Sequence[str]
    context: Mapping[str, Any] = field(default_factory=dict)
    locale: str | None = None
    cc: Sequence[str] = ()
    bcc: Sequence[str] = ()
    reply_to: Sequence[str] = ()
    resource: str | None = None
    entity_id: Any = None


type ContextFactory = Callable[[], Mapping[str, Any]] | Mapping[str, Any]


class Mailer:
    """Composes messages from a template set and sends them.

    Args:
        sender: The ``EmailSender`` (``SmtpEmailSender``, ``SesEmailSender``,
            ``RecordingEmailSender`` in tests).
        templates: The ``TemplateRenderer``.
        from_address: ``From`` of every message.
        reply_to: Default ``Reply-To``.
        default_locale: Used when a request names none.
        prefix: Folder of the email templates (``email/``).
        globals: Values every template gets (app name, URLs), or a function
            returning them; the request's context wins on a clash.
        suppressions: A ``SuppressionList``: suppressed recipients are left
            out of every message, and a message with nobody left isn't sent.
    """

    def __init__(
        self,
        sender: EmailSender,
        templates: TemplateRenderer,
        *,
        from_address: str,
        reply_to: Sequence[str] = (),
        default_locale: str | None = None,
        prefix: str = "email/",
        globals: ContextFactory | None = None,
        suppressions: SuppressionList | None = None,
    ) -> None:
        if "@" not in parseaddr(from_address)[1]:
            raise ConfigurationError(f"Invalid from_address {from_address!r}")
        self.sender = sender
        self.templates = templates
        self.from_address = from_address
        self.reply_to = tuple(reply_to)
        self.default_locale = default_locale
        self.prefix = prefix
        self.globals = globals
        self.suppressions = suppressions

    @property
    def domain(self) -> str:
        return parseaddr(self.from_address)[1].rpartition("@")[2]

    def template_names(self, template: str) -> tuple[str, str, str]:
        """The subject, text and html template names of a set."""
        base = f"{self.prefix}{template}"
        return f"{base}.subject.txt", f"{base}.txt", f"{base}.html"

    def compose(
        self,
        template: str,
        to: Sequence[str] | str,
        context: Mapping[str, Any] | None = None,
        *,
        locale: str | None = None,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
        reply_to: Sequence[str] = (),
        attachments: Sequence[Attachment] = (),
        message_id: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> EmailMessage:
        """Render a template set into a message (nothing is sent)."""
        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients:
            raise ConfigurationError(f"Email {template!r} has no recipient")
        values = dict(self.globals() if callable(self.globals) else self.globals or {})
        values.update(context or {})
        locale = locale or self.default_locale
        subject_name, text_name, html_name = self.template_names(template)
        subject = self.templates.render(subject_name, values, locale=locale)
        text = self._optional(text_name, values, locale)
        html = self._optional(html_name, values, locale)
        if text is None and html is None:
            raise TemplateNotFound(
                f"Email {template!r} has no body: add {text_name} or {html_name}",
                details={"name": text_name, "locale": locale, "tried": [text_name, html_name]},
                doc="docs/email.md#templates",
            )
        return EmailMessage(
            sender=self.from_address,
            to=recipients,
            subject=" ".join(subject.split()),
            text=text,
            html=html,
            cc=tuple(cc),
            bcc=tuple(bcc),
            reply_to=tuple(reply_to) or self.reply_to,
            message_id=message_id,
            headers=dict(headers or {}),
            attachments=tuple(attachments),
            tags={"template": template},
        )

    def _optional(self, name: str, values: Mapping[str, Any], locale: str | None) -> str | None:
        if not self.templates.exists(name, locale=locale):
            return None
        return self.templates.render(name, values, locale=locale)

    async def send(
        self, template: str, to: Sequence[str] | str, context=None, **kw: Any
    ) -> str | None:  # type: ignore[no-untyped-def]
        """Compose and send now (outside a transaction: a job, a script)."""
        message = self.compose(template, to, context, **kw)
        message = await self._without_suppressed(message)
        if message is None:
            log.info("email skipped: every recipient is suppressed", extra={"template": template})
            return None
        provider_id = await self.sender.send(message)
        log.info(
            "email sent",
            extra={"template": template, "recipients": len(message.recipients),
                   "message_id": message.message_id, "provider_id": provider_id},
        )  # fmt: skip
        return provider_id

    async def _without_suppressed(self, message: EmailMessage) -> EmailMessage | None:
        if self.suppressions is None:
            return message
        blocked = {a.lower() for a in await self.suppressions.suppressed(message.recipients)}
        if not blocked:
            return message

        def keep(addresses: Sequence[str]) -> tuple[str, ...]:
            return tuple(a for a in addresses if parseaddr(a)[1].lower() not in blocked)

        to, cc, bcc = keep(message.to), keep(message.cc), keep(message.bcc)
        if not (to or cc or bcc):
            return None
        return dataclasses.replace(message, to=to, cc=cc, bcc=bcc)

    # ---------------------------------------------------- requested emails

    async def handle(self, envelope: EventEnvelope) -> None:
        """Send the email an ``EmailRequested`` envelope asks for. Its
        ``Message-ID`` comes from the envelope id, so a retry is the same message."""
        data = dict(envelope.payload)
        await self.send(
            str(data["template"]),
            list(data.get("to") or ()),
            dict(data.get("context") or {}),
            locale=data.get("locale"),
            cc=list(data.get("cc") or ()),
            bcc=list(data.get("bcc") or ()),
            reply_to=list(data.get("reply_to") or ()),
            message_id=f"<{envelope.id}@{self.domain}>",
        )

    def router(self, *, name: str = "emails") -> EventRouter:
        """An ``EventRouter`` that sends ``EmailRequested`` and ignores the rest.

        Run it through ``MessageProcessor(..., manager=)`` (the inbox keeps a
        request from being sent twice) and combine it with other routers with
        ``EventRouter.include``. Delivery is at least once: a crash between the
        provider's answer and the inbox mark sends the message again, with the
        same ``Message-ID``.
        """
        router = EventRouter(ignore_unknown=True, name=name, description="Sends requested emails")
        router.register(EmailRequested, lambda envelope: self.handle(envelope))
        return router


class InMemorySuppressionList:
    """A ``SuppressionList`` in this process (tests, single instances); use the
    SQL one (``SqlAlchemySuppressionList``) with several."""

    def __init__(self) -> None:
        self.reasons: dict[str, str] = {}

    async def suppressed(self, addresses: Sequence[str]) -> set[str]:
        wanted = {parseaddr(a)[1].lower() or a.lower() for a in addresses}
        return wanted & set(self.reasons)

    async def suppress(self, address: str, reason: str) -> None:
        self.reasons[address.lower()] = reason

    async def release(self, address: str) -> None:
        self.reasons.pop(address.lower(), None)


class EmailEventHandler:
    """Acts on what mail services report: hard bounces, complaints and
    unsubscribes suppress the address; soft bounces and deliveries are logged.

    Args:
        suppressions: Where suppressed addresses go.
        on_event: Also called for every event (metrics, a record's status).
    """

    SUPPRESS = {"complaint", "unsubscribe"}

    def __init__(
        self, suppressions: SuppressionList, *, on_event: Callable[[EmailEvent], Any] | None = None
    ) -> None:
        self.suppressions = suppressions
        self.on_event = on_event

    async def handle(self, events: Sequence[EmailEvent]) -> int:
        """Returns how many addresses were suppressed."""
        suppressed = 0
        for event in events:
            if event.kind in self.SUPPRESS or (event.kind == "bounce" and event.permanent):
                await self.suppressions.suppress(
                    event.address, f"{event.provider} {event.kind}: {event.reason}".strip()
                )
                suppressed += 1
            log.info(
                "email %s",
                event.kind,
                extra={"provider": event.provider, "permanent": event.permanent},
            )
            if self.on_event is not None:
                result = self.on_event(event)
                if hasattr(result, "__await__"):
                    await result
        return suppressed


def to_mime(message: EmailMessage) -> MimeMessage:
    """The message as a MIME document (SMTP, SES raw). ``Bcc`` is not a header:
    pass ``message.recipients`` as the envelope recipients."""
    mime = MimeMessage()
    mime["From"] = message.sender
    mime["To"] = ", ".join(message.to)
    if message.cc:
        mime["Cc"] = ", ".join(message.cc)
    if message.reply_to:
        mime["Reply-To"] = ", ".join(message.reply_to)
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=False, usegmt=True)
    if message.message_id:
        mime["Message-ID"] = message.message_id
    for name, value in message.headers.items():
        mime[name] = value
    if message.text is not None:
        mime.set_content(message.text)
        if message.html is not None:
            mime.add_alternative(message.html, subtype="html")
    else:
        mime.set_content(message.html or "", subtype="html")
    for attachment in message.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        mime.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return mime


def attachment(filename: str, content: bytes, content_type: str | None = None) -> Attachment:
    """An attachment, its type guessed from the file name when not given."""
    guessed = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Attachment(filename, guessed, content)


def jsonable_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """``context`` as it reaches the template from an ``EmailRequested``."""
    return to_jsonable(dict(context))


__all__ = [
    "Attachment",
    "EmailEvent",
    "EmailEventHandler",
    "InMemorySuppressionList",
    "EmailMessage",
    "EmailRequested",
    "Mailer",
    "attachment",
    "jsonable_context",
    "to_mime",
]
