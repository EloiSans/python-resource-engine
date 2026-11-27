"""EventGridPublisher: CloudEvents batches to a topic (mock transport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope
from resource_engine_azure import EventGridPublisher


def envelope(n: int, size: int = 10) -> EventEnvelope:
    return EventEnvelope(
        type="InvoiceApproved", payload={"n": n, "pad": "x" * size}, occurred_at=datetime.now(UTC)
    )


async def test_batches_of_cloudevents_with_the_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    publisher = EventGridPublisher(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        "https://t.westeurope-1.eventgrid.azure.net/api/events",
        source="/invoices",
        key="k",
        type_prefix="com.example.",
    )
    await publisher.publish([envelope(1), envelope(2)])
    [request] = seen
    assert request.headers["aeg-sas-key"] == "k"
    assert request.headers["content-type"].startswith("application/cloudevents-batch+json")
    events = json.loads(request.content)
    assert [e["type"] for e in events] == ["com.example.InvoiceApproved"] * 2
    assert events[0]["source"] == "/invoices" and events[0]["specversion"] == "1.0"
    big = [envelope(n, size=300_000) for n in range(5)]
    assert len(publisher.batches(big)) >= 2  # under the 1 MB limit per request


async def test_refusals_report_the_failed_ids():
    async def token():
        return "t"

    publisher = EventGridPublisher(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))),
        "https://t/api/events",
        source="/x",
        token=token,
    )
    sent = [envelope(1)]
    with pytest.raises(PublishFailed) as info:
        await publisher.publish(sent)
    assert info.value.failed_ids == [sent[0].id]
    with pytest.raises(ValueError):
        EventGridPublisher(httpx.AsyncClient(), "https://t", source="/x")
