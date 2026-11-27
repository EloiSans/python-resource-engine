"""Email through providers' HTTP APIs (Postmark, SendGrid, Microsoft Graph),
and their bounce and complaint webhooks as ``EmailEvent``s.

```python
from resource_engine_http import PostmarkEmailSender

sender = PostmarkEmailSender(http, token=SERVER_TOKEN)
```

- **Errors:** a refusal for good (an invalid or inactive recipient, a sender
  the account may not use) is ``EmailRejected`` (RE-E502, dead-lettered); a
  429, a 5xx or a connection problem is ``RemoteCallFailed`` (RE-E501,
  retried).
- **Webhooks:** ``parse_postmark`` and ``parse_sendgrid`` turn the providers'
  event payloads into ``EmailEvent``s for ``EmailEventHandler``
  (``mount_email_events`` in ``resource-engine-fastapi``).
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from email.utils import parseaddr
from typing import Any

import httpx
from resource_engine.calls import outgoing_call
from resource_engine.errors import EmailRejected
from resource_engine.ports.email import EmailEvent, EmailMessage

from resource_engine_http.errors import check_response, transport_failed


async def _post(
    client: httpx.AsyncClient,
    service: str,
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    rejected: Callable[[httpx.Response], bool],
    recipients: Sequence[str],
) -> httpx.Response:
    try:
        async with outgoing_call(service, "send", method="POST", url=url) as call:
            response = await client.post(url, json=payload, headers=dict(headers))
            call.status = response.status_code
    except httpx.HTTPError as exc:
        raise transport_failed(exc, service=service, operation="send") from exc
    if rejected(response):
        raise EmailRejected(
            f"{service} refused the message: HTTP {response.status_code}",
            details={"recipients": list(recipients), "reason": response.text[:500]},
            doc="docs/email.md",
        )
    check_response(response, service=service, operation="send")
    return response


def _attachments(message: EmailMessage) -> list[tuple[str, str, str]]:
    return [
        (a.filename, a.content_type, base64.b64encode(a.content).decode())
        for a in message.attachments
    ]


def _headers(message: EmailMessage) -> dict[str, str]:
    headers = dict(message.headers)
    if message.message_id:
        headers.setdefault("Message-ID", message.message_id)
    return headers


# ---------------------------------------------------------------- Postmark


class PostmarkEmailSender:
    """``EmailSender`` over Postmark's ``/email`` API.

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        token: The server token (``X-Postmark-Server-Token``).
        message_stream: ``outbound`` (transactional) or another stream.
        url: The API (``https://api.postmarkapp.com``).
    """

    service = "postmark"
    #: Postmark error codes that are permanent: invalid or inactive
    #: recipients, sender signature not confirmed.
    PERMANENT = {300, 400, 401, 405, 406, 409, 411, 412, 413}

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        message_stream: str = "outbound",
        url: str = "https://api.postmarkapp.com",
    ) -> None:
        self.client = client
        self.token = token
        self.message_stream = message_stream
        self.url = url.rstrip("/") + "/email"

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        body: dict[str, Any] = {
            "From": message.sender,
            "To": ", ".join(message.to),
            "Subject": message.subject,
            "MessageStream": self.message_stream,
        }
        optional = {
            "Cc": ", ".join(message.cc),
            "Bcc": ", ".join(message.bcc),
            "ReplyTo": ", ".join(message.reply_to),
            "TextBody": message.text,
            "HtmlBody": message.html,
            "Tag": message.tags.get("template"),
        }
        body.update({k: v for k, v in optional.items() if v})
        headers = _headers(message)
        if headers:
            body["Headers"] = [{"Name": k, "Value": v} for k, v in headers.items()]
        if message.tags:
            body["Metadata"] = dict(message.tags)
        if message.attachments:
            body["Attachments"] = [
                {"Name": name, "ContentType": kind, "Content": content}
                for name, kind, content in _attachments(message)
            ]
        return body

    def _rejected(self, response: httpx.Response) -> bool:
        if response.status_code != 422:
            return False
        try:
            return int(response.json().get("ErrorCode", 0)) in self.PERMANENT
        except ValueError:
            return False

    async def send(self, message: EmailMessage) -> str | None:
        response = await _post(
            self.client,
            self.service,
            self.url,
            self.payload(message),
            {"X-Postmark-Server-Token": self.token, "Accept": "application/json"},
            rejected=self._rejected,
            recipients=message.recipients,
        )
        return response.json().get("MessageID")


def parse_postmark(payload: Mapping[str, Any]) -> list[EmailEvent]:
    """A Postmark webhook (bounce, spam complaint, delivery, subscription change)."""
    kind = payload.get("RecordType")
    address = payload.get("Email") or payload.get("Recipient") or ""
    message_id = payload.get("MessageID")
    if kind == "Bounce":
        hard = payload.get("Type") in {"HardBounce", "BadEmailAddress", "ManuallyDeactivated"}
        reason = payload.get("Description") or payload.get("Type", "")
        return [EmailEvent("bounce", address, hard, reason, "postmark", message_id)]
    if kind == "SpamComplaint":
        return [EmailEvent("complaint", address, True, "spam complaint", "postmark", message_id)]
    if kind == "Delivery":
        return [EmailEvent("delivery", address, False, "", "postmark", message_id)]
    if kind == "SubscriptionChange" and payload.get("SuppressSending"):
        reason = payload.get("SuppressionReason", "")
        return [EmailEvent("unsubscribe", address, True, reason, "postmark", message_id)]
    return []


# ---------------------------------------------------------------- SendGrid


class SendGridEmailSender:
    """``EmailSender`` over SendGrid's ``/v3/mail/send`` API.

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        api_key: A SendGrid API key with Mail Send.
        url: The API (``https://api.sendgrid.com``; ``https://api.eu.sendgrid.com``
            for EU data residency).
    """

    service = "sendgrid"

    def __init__(
        self, client: httpx.AsyncClient, *, api_key: str, url: str = "https://api.sendgrid.com"
    ) -> None:
        self.client = client
        self.api_key = api_key
        self.url = url.rstrip("/") + "/v3/mail/send"

    @staticmethod
    def _address(value: str) -> dict[str, str]:
        name, address = parseaddr(value)
        return {"email": address or value, **({"name": name} if name else {})}

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        personalization: dict[str, Any] = {"to": [self._address(a) for a in message.to]}
        if message.cc:
            personalization["cc"] = [self._address(a) for a in message.cc]
        if message.bcc:
            personalization["bcc"] = [self._address(a) for a in message.bcc]
        content = []
        if message.text is not None:
            content.append({"type": "text/plain", "value": message.text})
        if message.html is not None:
            content.append({"type": "text/html", "value": message.html})
        body: dict[str, Any] = {
            "personalizations": [personalization],
            "from": self._address(message.sender),
            "subject": message.subject,
            "content": content,
        }
        if message.reply_to:
            body["reply_to_list"] = [self._address(a) for a in message.reply_to]
        headers = {k: v for k, v in _headers(message).items() if k.lower() != "message-id"}
        if headers:
            body["headers"] = headers
        if message.tags:
            body["custom_args"] = dict(message.tags)
            if "template" in message.tags:
                body["categories"] = [message.tags["template"][:255]]
        if message.attachments:
            body["attachments"] = [
                {"filename": name, "type": kind, "content": data, "disposition": "attachment"}
                for name, kind, data in _attachments(message)
            ]
        return body

    async def send(self, message: EmailMessage) -> str | None:
        response = await _post(
            self.client,
            self.service,
            self.url,
            self.payload(message),
            {"Authorization": f"Bearer {self.api_key}"},
            rejected=lambda r: r.status_code == 400,
            recipients=message.recipients,
        )
        return response.headers.get("X-Message-Id")


def parse_sendgrid(payload: Sequence[Mapping[str, Any]]) -> list[EmailEvent]:
    """A SendGrid Event Webhook batch (bounce, dropped, spamreport, delivered,
    unsubscribe)."""
    events = []
    for item in payload:
        kind, address = item.get("event"), item.get("email", "")
        message_id = item.get("sg_message_id")
        reason = item.get("reason") or item.get("response") or ""
        if kind == "bounce":
            hard = item.get("type", "bounce") == "bounce"  # "blocked" is a soft bounce
            events.append(EmailEvent("bounce", address, hard, reason, "sendgrid", message_id))
        elif kind == "dropped":
            permanent = "Bounced" in reason or "Invalid" in reason
            events.append(EmailEvent("bounce", address, permanent, reason, "sendgrid", message_id))
        elif kind == "spamreport":
            events.append(
                EmailEvent("complaint", address, True, "spam report", "sendgrid", message_id)
            )
        elif kind in ("unsubscribe", "group_unsubscribe"):
            events.append(EmailEvent("unsubscribe", address, True, kind, "sendgrid", message_id))
        elif kind == "delivered":
            events.append(EmailEvent("delivery", address, False, "", "sendgrid", message_id))
    return events


# ------------------------------------------------------- Microsoft Graph


class GraphEmailSender:
    """``EmailSender`` over Microsoft Graph ``sendMail``, from a Microsoft 365
    mailbox (an application with the ``Mail.Send`` permission).

    Args:
        client: An ``httpx.AsyncClient`` authenticated for Graph, for example
            with ``OAuth2ClientCredentials`` (scope
            ``https://graph.microsoft.com/.default``).
        mailbox: The sending user's id or address.
        save_to_sent_items: Keep a copy in the mailbox's Sent Items.
        url: The Graph endpoint.

    Graph accepts the message (202) and delivers it later; failures come back
    as non-delivery reports to the mailbox, not as events.
    """

    service = "graph"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        mailbox: str,
        save_to_sent_items: bool = False,
        url: str = "https://graph.microsoft.com/v1.0",
    ) -> None:
        self.client = client
        self.mailbox = mailbox
        self.save_to_sent_items = save_to_sent_items
        self.url = f"{url.rstrip('/')}/users/{mailbox}/sendMail"

    @staticmethod
    def _recipient(value: str) -> dict[str, Any]:
        name, address = parseaddr(value)
        return {"emailAddress": {"address": address or value, **({"name": name} if name else {})}}

    def payload(self, message: EmailMessage) -> dict[str, Any]:
        html = message.html is not None
        mail: dict[str, Any] = {
            "subject": message.subject,
            "body": {
                "contentType": "HTML" if html else "Text",
                "content": message.html if html else message.text or "",
            },
            "toRecipients": [self._recipient(a) for a in message.to],
        }
        if message.cc:
            mail["ccRecipients"] = [self._recipient(a) for a in message.cc]
        if message.bcc:
            mail["bccRecipients"] = [self._recipient(a) for a in message.bcc]
        if message.reply_to:
            mail["replyTo"] = [self._recipient(a) for a in message.reply_to]
        custom = {k: v for k, v in message.headers.items() if k.lower().startswith("x-")}
        if custom:  # Graph only takes custom X- headers
            mail["internetMessageHeaders"] = [{"name": k, "value": v} for k, v in custom.items()]
        if message.attachments:
            mail["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": name,
                    "contentType": kind,
                    "contentBytes": data,
                }
                for name, kind, data in _attachments(message)
            ]
        return {"message": mail, "saveToSentItems": self.save_to_sent_items}

    async def send(self, message: EmailMessage) -> str | None:
        await _post(
            self.client,
            self.service,
            self.url,
            self.payload(message),
            {},
            rejected=lambda r: r.status_code == 400,
            recipients=message.recipients,
        )
        return None


__all__ = [
    "GraphEmailSender",
    "PostmarkEmailSender",
    "SendGridEmailSender",
    "parse_postmark",
    "parse_sendgrid",
]
