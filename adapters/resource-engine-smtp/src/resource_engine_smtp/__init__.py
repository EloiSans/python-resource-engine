"""SMTP for Resource Engine: an ``EmailSender`` for any SMTP server (Mailpit
locally, Postfix, a provider's relay: SES, Postmark, SendGrid, Microsoft 365).

```python
from resource_engine_smtp import SmtpEmailSender

sender = SmtpEmailSender("smtp.example.com", 587, username="api", password=SECRET)
```

- **TLS:** port 465 uses implicit TLS; other ports upgrade with STARTTLS when
  the server offers it (``starttls=True`` makes it required). ``tls=False`` is
  for a local catcher only.
- **Errors:** 5xx answers (unknown recipient, refused sender) are
  ``EmailRejected`` (RE-E502, not retried); connection problems and 4xx
  answers are ``RemoteCallFailed`` (RE-E501, retried).
- **Connections:** one per message; enough for transactional email. Use the
  provider's HTTP API adapter for bulk sending.
"""

from __future__ import annotations

import aiosmtplib
from resource_engine.email import to_mime
from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage


class SmtpEmailSender:
    """``EmailSender`` over SMTP (``aiosmtplib``).

    Args:
        host / port: The server (587 submission, 465 implicit TLS, 25 relay).
        username / password: SMTP AUTH credentials, if the server needs them.
        starttls: ``None`` upgrades when offered; ``True`` requires it.
        tls: ``False`` disables TLS entirely (a local catcher such as Mailpit).
        timeout: Seconds for connecting and each command.
    """

    def __init__(
        self,
        host: str,
        port: int = 587,
        *,
        username: str | None = None,
        password: str | None = None,
        starttls: bool | None = None,
        tls: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.use_tls = tls and port == 465
        self.start_tls = False if not tls or port == 465 else starttls

    async def send(self, message: EmailMessage) -> str | None:
        mime = to_mime(message)
        try:
            await aiosmtplib.send(
                mime,
                sender=_address(message.sender),
                recipients=[_address(r) for r in message.recipients],
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                use_tls=self.use_tls,
                start_tls=self.start_tls,
                timeout=self.timeout,
            )
        except aiosmtplib.SMTPRecipientsRefused as exc:
            refused = [str(r.recipient) for r in exc.recipients]
            raise _rejected(refused, "recipients refused", exc) from exc
        except aiosmtplib.SMTPResponseException as exc:
            if 500 <= exc.code < 600:
                raise _rejected(message.recipients, exc.message, exc) from exc
            raise _unreachable(f"{exc.code} {exc.message}", exc) from exc
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            raise _unreachable(str(exc) or type(exc).__name__, exc) from exc
        return mime["Message-ID"]


def _address(value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(value)[1] or value


def _rejected(recipients, reason: str, exc: Exception) -> EmailRejected:  # type: ignore[no-untyped-def]
    return EmailRejected(
        f"The SMTP server refused the message: {reason}",
        details={"recipients": list(recipients), "reason": reason},
        hint="Check the addresses and the sender's permission to send for its domain.",
        doc="docs/email.md",
    )


def _unreachable(reason: str, exc: Exception) -> RemoteCallFailed:
    return RemoteCallFailed(f"SMTP failed: {reason}", service="smtp", operation="send", body=reason)


__all__ = ["SmtpEmailSender"]
