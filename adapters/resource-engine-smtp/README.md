# resource-engine-smtp

SMTP for Resource Engine, on `aiosmtplib`: `SmtpEmailSender`, an
`EmailSender` for any SMTP server or provider relay (SES, Postmark,
SendGrid, Microsoft 365, Postfix).

- STARTTLS or implicit TLS (port 465); no TLS only for a local catcher.
- Permanent refusals (5xx) are `EmailRejected` (RE-E502, dead-lettered);
  connection problems and 4xx answers `RemoteCallFailed` (RE-E501, retried).

Tested against Mailpit (`axllent/mailpit`, see `compose.emulators.yaml`,
SMTP on port 1026). See [email](../../docs/email.md) and the
[adapter catalogue](../../docs/adapters.md). Part of [Resource Engine](../../README.md).
