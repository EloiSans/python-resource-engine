"""An ``EmailSender`` that keeps the messages, for tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage


@dataclass
class RecordingEmailSender:
    """Keeps every message in ``sent``. ``reject`` lists addresses refused for
    good (RE-E502); ``failures`` makes the next sends fail as unreachable (RE-E501)."""

    sent: list[EmailMessage] = field(default_factory=list)
    reject: set[str] = field(default_factory=set)
    failures: int = 0

    async def send(self, message: EmailMessage) -> str | None:
        if self.failures > 0:
            self.failures -= 1
            raise RemoteCallFailed("mail service down", service="email", operation="send")
        refused = [r for r in message.recipients if r in self.reject]
        if refused:
            raise EmailRejected(
                "Recipient refused", details={"recipients": refused, "reason": "test"}
            )
        self.sent.append(message)
        return f"recorded-{len(self.sent)}"

    def to(self, address: str) -> list[EmailMessage]:
        """Messages that went to ``address``."""
        return [m for m in self.sent if address in m.recipients]
