"""Amazon SES (v2 API): an ``EmailSender`` that sends the MIME document
(``Content.Raw``), so attachments, ``Message-ID`` and custom headers go
through as composed.

```python
session = aiobotocore.session.get_session()
async with session.create_client("sesv2", region_name="eu-west-1") as ses:
    sender = SesEmailSender(ses, configuration_set="transactional")
```

- **Errors:** ``MessageRejected``, ``MailFromDomainNotVerifiedException`` and
  ``AccountSuspendedException`` are ``EmailRejected`` (RE-E502, not retried);
  throttling and other failures ``RemoteCallFailed`` (RE-E501, retried).
- **Tags:** the message's ``tags`` (the ``template`` name by default) become
  SES message tags, for the configuration set's event destinations.
"""

from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any

from resource_engine.email import to_mime
from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage

PERMANENT = {
    "MessageRejected",
    "MailFromDomainNotVerifiedException",
    "AccountSuspendedException",
    "SendingPausedException",
    "BadRequestException",
}

_TAG = re.compile(r"[^A-Za-z0-9_-]")


class SesEmailSender:
    """``EmailSender`` over SES v2 ``SendEmail`` with raw content.

    Args:
        client: An aiobotocore ``sesv2`` client, owned and closed by the caller.
        configuration_set: SES configuration set (event destinations, IP pool).
        from_arn: Sending authorisation identity ARN, when sending for another account.
    """

    def __init__(
        self, client: Any, *, configuration_set: str | None = None, from_arn: str | None = None
    ) -> None:
        self.client = client
        self.configuration_set = configuration_set
        self.from_arn = from_arn

    async def send(self, message: EmailMessage) -> str | None:
        from botocore.exceptions import BotoCoreError, ClientError

        request: dict[str, Any] = {
            # the bare address: the display name is in the raw From header
            "FromEmailAddress": parseaddr(message.sender)[1] or message.sender,
            "Destination": {
                "ToAddresses": list(message.to),
                "CcAddresses": list(message.cc),
                "BccAddresses": list(message.bcc),
            },
            "Content": {"Raw": {"Data": to_mime(message).as_bytes()}},
        }
        if self.configuration_set:
            request["ConfigurationSetName"] = self.configuration_set
        if self.from_arn:
            request["FromEmailAddressIdentityArn"] = self.from_arn
        if message.tags:
            request["EmailTags"] = [
                {"Name": _TAG.sub("_", k)[:256], "Value": _TAG.sub("_", v)[:256]}
                for k, v in message.tags.items()
            ]
        try:
            response = await self.client.send_email(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            reason = exc.response.get("Error", {}).get("Message", str(exc))
            if code in PERMANENT:
                raise EmailRejected(
                    f"SES refused the message: {reason}",
                    details={"recipients": message.recipients, "reason": f"{code}: {reason}"},
                    hint="Verify the sender identity (and, in the sandbox, the recipients).",
                    doc="docs/email.md",
                ) from exc
            raise RemoteCallFailed(
                f"SES failed: {code} {reason}", service="ses", operation="send_email", body=reason
            ) from exc
        except BotoCoreError as exc:
            raise RemoteCallFailed(
                f"SES unreachable: {exc}", service="ses", operation="send_email"
            ) from exc
        return response.get("MessageId")


__all__ = ["SesEmailSender"]
