"""mount_email_events: provider webhooks, authenticated by a shared token."""

from __future__ import annotations

import base64

import httpx
from fastapi import FastAPI
from resource_engine.email import EmailEventHandler, InMemorySuppressionList
from resource_engine.ports.email import EmailEvent
from resource_engine_fastapi import mount_email_events


def parse(payload):
    return [EmailEvent("bounce", payload["email"], permanent=True)] if "email" in payload else []


async def test_webhook():
    suppressions = InMemorySuppressionList()
    app = FastAPI()
    mount_email_events(app, "/hooks/mail", parse, EmailEventHandler(suppressions), token="s3")
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
    assert (await api.post("/hooks/mail", json={"email": "a@x.com"})).status_code == 401
    wrong = await api.post("/hooks/mail?token=nope", json={"email": "a@x.com"})
    assert wrong.status_code == 401
    ok = await api.post("/hooks/mail?token=s3", json={"email": "a@x.com"})
    assert ok.status_code == 204 and await suppressions.suppressed(["a@x.com"]) == {"a@x.com"}
    basic = {"Authorization": "Basic " + base64.b64encode(b"postmark:s3").decode()}
    assert (await api.post("/hooks/mail", json={"other": 1}, headers=basic)).status_code == 204
    assert (await api.post("/hooks/mail?token=s3", content=b"{")).status_code == 400
