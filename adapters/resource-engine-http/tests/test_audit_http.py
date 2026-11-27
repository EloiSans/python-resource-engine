"""Audit entries over HTTP: JSON lines, Splunk HEC, Elastic bulk, and errors."""

from __future__ import annotations

import json

import httpx
import pytest
from resource_engine.audit import AuditEntry
from resource_engine.errors import MessageRejected, RemoteCallFailed
from resource_engine_http import HttpAuditExporter

ENTRY = AuditEntry(resource="notes", operation="create", action="create", outcome="ok")


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_formats():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("_bulk"):
            return httpx.Response(200, json={"errors": False, "items": []})
        return httpx.Response(200, json={"text": "Success"})

    http = client(handler)
    await HttpAuditExporter(http, "https://logs/ingest").export([ENTRY, ENTRY])
    await HttpAuditExporter.splunk_hec(
        http, "https://splunk:8088", token="t", index="audit"
    ).export([ENTRY])
    await HttpAuditExporter.elastic(http, "https://es:9200", index="audit", api_key="k").export(
        [ENTRY]
    )
    lines, splunk, elastic = seen
    assert lines.headers["content-type"] == "application/x-ndjson"
    assert [json.loads(line)["id"] for line in lines.content.decode().splitlines()] == [
        ENTRY.id
    ] * 2
    assert splunk.url.path == "/services/collector/event"
    assert splunk.headers["authorization"] == "Splunk t"
    event = json.loads(splunk.content)
    assert event["index"] == "audit" and event["event"]["resource"] == "notes"
    action, document = (json.loads(x) for x in elastic.content.decode().splitlines())
    assert action == {"index": {"_index": "audit", "_id": ENTRY.id}}
    assert (
        document["@timestamp"] == document["at"] and elastic.headers["authorization"] == "ApiKey k"
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, RemoteCallFailed), (429, RemoteCallFailed), (400, MessageRejected)],
)
async def test_failures(status, expected):
    exporter = HttpAuditExporter(client(lambda r: httpx.Response(status)), "https://logs/ingest")
    with pytest.raises(expected):
        await exporter.export([ENTRY])


async def test_elastic_item_errors_and_transport():
    bulk = {"errors": True, "items": [{"index": {"error": {"type": "mapper_parsing_exception"}}}]}
    exporter = HttpAuditExporter.elastic(
        client(lambda r: httpx.Response(200, json=bulk)), "https://es", index="audit"
    )
    with pytest.raises(MessageRejected):
        await exporter.export([ENTRY])

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(RemoteCallFailed):
        await HttpAuditExporter(client(down), "https://logs/ingest").export([ENTRY])
    with pytest.raises(ValueError):
        HttpAuditExporter(client(down), "https://es/_bulk", format="elastic_bulk")
