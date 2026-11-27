"""Postmark, SendGrid and Microsoft Graph senders, and the webhook parsers."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from resource_engine.email import attachment
from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage
from resource_engine_http import (
    GraphEmailSender,
    PostmarkEmailSender,
    SendGridEmailSender,
    parse_postmark,
    parse_sendgrid,
)

MESSAGE = EmailMessage(
    sender="Factures <factures@example.com>",
    to=["Ada <ada@example.com>"],
    cc=["cc@example.com"],
    bcc=["bcc@example.com"],
    reply_to=["help@example.com"],
    subject="Hola",
    text="Hola Ada",
    html="<p>Hola Ada</p>",
    message_id="<1@example.com>",
    headers={"X-Campaign": "approval"},
    attachments=[attachment("a.pdf", b"%PDF")],
    tags={"template": "invoice_approved"},
)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_postmark():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"MessageID": "pm-1", "ErrorCode": 0})

    sender = PostmarkEmailSender(client(handler), token="t")
    assert await sender.send(MESSAGE) == "pm-1"
    body = json.loads(seen[0].content)
    assert seen[0].headers["x-postmark-server-token"] == "t"
    assert body["To"] == "Ada <ada@example.com>" and body["Bcc"] == "bcc@example.com"
    assert body["TextBody"] == "Hola Ada" and body["Tag"] == "invoice_approved"
    assert {"Name": "Message-ID", "Value": "<1@example.com>"} in body["Headers"]
    assert base64.b64decode(body["Attachments"][0]["Content"]) == b"%PDF"

    inactive = PostmarkEmailSender(
        client(lambda r: httpx.Response(422, json={"ErrorCode": 406, "Message": "inactive"})),
        token="t",
    )
    with pytest.raises(EmailRejected):
        await inactive.send(MESSAGE)
    down = PostmarkEmailSender(client(lambda r: httpx.Response(503)), token="t")
    with pytest.raises(RemoteCallFailed):
        await down.send(MESSAGE)


async def test_sendgrid():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202, headers={"X-Message-Id": "sg-1"})

    assert await SendGridEmailSender(client(handler), api_key="k").send(MESSAGE) == "sg-1"
    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer k"
    assert body["personalizations"][0]["to"] == [{"email": "ada@example.com", "name": "Ada"}]
    assert body["from"] == {"email": "factures@example.com", "name": "Factures"}
    assert [c["type"] for c in body["content"]] == ["text/plain", "text/html"]
    assert body["categories"] == ["invoice_approved"] and "Message-ID" not in body["headers"]
    bad = SendGridEmailSender(client(lambda r: httpx.Response(400, json={})), api_key="k")
    with pytest.raises(EmailRejected):
        await bad.send(MESSAGE)


async def test_graph():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(202)

    sender = GraphEmailSender(client(handler), mailbox="factures@example.com")
    assert await sender.send(MESSAGE) is None
    assert seen[0].url.path == "/v1.0/users/factures@example.com/sendMail"
    body = json.loads(seen[0].content)
    mail = body["message"]
    assert mail["body"] == {"contentType": "HTML", "content": "<p>Hola Ada</p>"}
    assert mail["toRecipients"][0]["emailAddress"] == {"address": "ada@example.com", "name": "Ada"}
    assert mail["internetMessageHeaders"] == [{"name": "X-Campaign", "value": "approval"}]
    assert mail["attachments"][0]["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert body["saveToSentItems"] is False


def test_postmark_webhooks():
    hard = parse_postmark(
        {"RecordType": "Bounce", "Type": "HardBounce", "Email": "x@y.com", "MessageID": "m"}
    )
    assert hard[0].kind == "bounce" and hard[0].permanent and hard[0].address == "x@y.com"
    soft = parse_postmark({"RecordType": "Bounce", "Type": "SoftBounce", "Email": "x@y.com"})
    assert not soft[0].permanent
    complaint = parse_postmark({"RecordType": "SpamComplaint", "Email": "x@y.com"})
    assert complaint[0].kind == "complaint"
    assert parse_postmark({"RecordType": "Open"}) == []


def test_sendgrid_webhooks():
    events = parse_sendgrid(
        [
            {"event": "bounce", "type": "bounce", "email": "a@x.com", "reason": "550 no user"},
            {"event": "bounce", "type": "blocked", "email": "b@x.com"},
            {"event": "spamreport", "email": "c@x.com"},
            {"event": "delivered", "email": "d@x.com"},
            {"event": "open", "email": "e@x.com"},
        ]
    )
    assert [(e.kind, e.address, e.permanent) for e in events] == [
        ("bounce", "a@x.com", True),
        ("bounce", "b@x.com", False),
        ("complaint", "c@x.com", True),
        ("delivery", "d@x.com", False),
    ]
