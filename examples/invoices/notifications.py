"""Emails of the example: Jinja templates shipped in the package
(``invoices/templates``, Catalan with an English fallback) and SMTP.

Locally, Mailpit catches every message (``docker compose -f
compose.emulators.yaml up -d mailpit``; UI at http://127.0.0.1:8026). In
production set ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USERNAME`` and
``SMTP_PASSWORD``; ``SMTP_TLS=0`` only for a local catcher.
"""

from __future__ import annotations

import os

from resource_engine.email import Mailer
from resource_engine.ports.email import EmailSender
from resource_engine_jinja import JinjaTemplates
from resource_engine_smtp import SmtpEmailSender

templates = JinjaTemplates(package="invoices", folder="templates", default_locale="ca")


def smtp_sender() -> SmtpEmailSender:
    host = os.getenv("SMTP_HOST", "127.0.0.1")
    return SmtpEmailSender(
        host,
        int(os.getenv("SMTP_PORT", "1026")),
        username=os.getenv("SMTP_USERNAME") or None,
        password=os.getenv("SMTP_PASSWORD") or None,
        tls=os.getenv("SMTP_TLS", "0" if host in ("127.0.0.1", "localhost") else "1") == "1",
    )


def build_mailer(sender: EmailSender | None = None) -> Mailer:
    return Mailer(
        sender or smtp_sender(),
        templates,
        from_address=os.getenv("EMAIL_FROM", "Factures <factures@invoices.example>"),
        default_locale="ca",
        globals={"app_name": "Factures · exemple de Resource Engine"},
    )
