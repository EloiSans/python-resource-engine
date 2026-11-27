"""Email port: hand a composed message to a mail service (SMTP, SES...).

``resource_engine.email`` composes messages from templates (``Mailer``) and
sends the ones requested inside a transaction after it commits
(``EmailRequested``). Adapters only deliver.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class EmailMessage:
    """A message ready to send.

    Attributes:
        sender: The ``From`` address (``"Factures <factures@example.com>"``).
        to / cc / bcc: Recipients; at least one in ``to``.
        subject: One line.
        text / html: The bodies; at least one. With both, clients pick.
        reply_to: ``Reply-To`` addresses.
        message_id: ``Message-ID`` (``<id@domain>``): stable across retries,
            so a message sent twice is recognisably the same one.
        headers: Extra headers (``List-Unsubscribe``...).
        attachments: Files attached to the message.
        tags: Provider metadata (SES tags...), never shown to recipients.
    """

    sender: str
    to: Sequence[str]
    subject: str
    text: str | None = None
    html: str | None = None
    cc: Sequence[str] = ()
    bcc: Sequence[str] = ()
    reply_to: Sequence[str] = ()
    message_id: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    attachments: Sequence[Attachment] = ()
    tags: Mapping[str, str] = field(default_factory=dict)

    @property
    def recipients(self) -> list[str]:
        return [*self.to, *self.cc, *self.bcc]


class EmailSender(Protocol):
    """Delivers a message; returns the provider's id when it gives one.

    Raises ``RemoteCallFailed`` (RE-E501) when the service can't be reached
    or refuses the message for a reason worth retrying, and
    ``EmailRejected`` (RE-E502) for a permanent refusal (an invalid address).
    """

    async def send(self, message: EmailMessage) -> str | None: ...


@dataclass(frozen=True)
class EmailEvent:
    """What a mail service reports after sending: a bounce, a complaint (the
    recipient marked it as spam), a delivery, an unsubscribe.

    Attributes:
        kind: ``bounce``, ``complaint``, ``delivery`` or ``unsubscribe``.
        address: The recipient it is about.
        permanent: For bounces: the address will never accept mail (hard
            bounce), not a full mailbox or a greylist (soft bounce).
        reason: The provider's description.
        provider: ``postmark``, ``sendgrid``, ``ses``...
        message_id: The provider's id of the message, when it gives one.
    """

    kind: str
    address: str
    permanent: bool = False
    reason: str = ""
    provider: str = ""
    message_id: str | None = None


class SuppressionList(Protocol):
    """Addresses not to send to (hard bounces, complaints, unsubscribes)."""

    async def suppressed(self, addresses: Sequence[str]) -> set[str]:
        """Which of ``addresses`` are suppressed (compared in lower case)."""
        ...

    async def suppress(self, address: str, reason: str) -> None: ...

    async def release(self, address: str) -> None:
        """Send to it again (the user fixed their mailbox, asked to resubscribe)."""
        ...
