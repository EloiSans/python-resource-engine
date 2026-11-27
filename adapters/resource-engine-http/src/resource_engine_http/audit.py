"""Audit entries over HTTP to a SIEM or log platform: plain JSON lines, the
Splunk HTTP Event Collector, or Elasticsearch / OpenSearch bulk indexing.

```python
from resource_engine_http import HttpAuditExporter

siem = HttpAuditExporter.splunk_hec(http, "https://splunk:8088", token=SECRET, index="audit")
forwarder = AuditForwarder(siem)
```

Each call sends a batch. A 2xx accepts it. A 429 or 5xx, or a connection
problem, is ``RemoteCallFailed`` (retried by the forwarder). Any other 4xx is
``MessageRejected``, which dead-letters the entries, since sending them again
won't help. Every entry carries its ``id``, so the receiver can drop repeats
(Elastic uses it as the document id).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from resource_engine.audit.export import entry_to_dict
from resource_engine.audit.model import AuditEntry
from resource_engine.calls import outgoing_call
from resource_engine.errors import MessageRejected

from resource_engine_http.errors import check_response, transport_failed

type AuditFormat = Literal["jsonl", "splunk_hec", "elastic_bulk"]


class HttpAuditExporter:
    """``AuditExporter`` that POSTs entries to an HTTP endpoint.

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (TLS, timeouts, proxies).
        url: The endpoint.
        format: ``jsonl`` (one JSON object per line), ``splunk_hec`` (events
            for the HTTP Event Collector) or ``elastic_bulk`` (``_bulk`` index
            actions).
        headers: Extra headers (authentication).
        index: The Splunk index or the Elasticsearch index.
        source: The Splunk ``sourcetype`` / ``source``.
    """

    service = "audit-http"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        format: AuditFormat = "jsonl",
        headers: dict[str, str] | None = None,
        index: str | None = None,
        source: str = "resource-engine:audit",
    ) -> None:
        if format == "elastic_bulk" and not index:
            raise ValueError("elastic_bulk needs an index")
        self.client = client
        self.url = url
        self.format = format
        self.headers = dict(headers or {})
        self.index = index
        self.source = source

    @classmethod
    def splunk_hec(
        cls, client: httpx.AsyncClient, base_url: str, *, token: str, index: str | None = None
    ) -> HttpAuditExporter:
        """Splunk's HTTP Event Collector (``/services/collector/event``)."""
        return cls(
            client,
            base_url.rstrip("/") + "/services/collector/event",
            format="splunk_hec",
            headers={"Authorization": f"Splunk {token}"},
            index=index,
        )

    @classmethod
    def elastic(
        cls, client: httpx.AsyncClient, base_url: str, *, index: str, api_key: str | None = None
    ) -> HttpAuditExporter:
        """Elasticsearch or OpenSearch ``_bulk``, one document per entry, ``_id`` = entry id."""
        headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}
        return cls(
            client,
            base_url.rstrip("/") + "/_bulk",
            format="elastic_bulk",
            headers=headers,
            index=index,
        )

    def body(self, entries: Sequence[AuditEntry]) -> tuple[str, str]:
        """``(content, content type)`` for a batch."""
        documents = [entry_to_dict(e) for e in entries]
        if self.format == "splunk_hec":
            events = []
            for entry, document in zip(entries, documents, strict=True):
                event: dict[str, Any] = {
                    "time": entry.at.timestamp(),
                    "sourcetype": self.source,
                    "event": document,
                }
                if self.index:
                    event["index"] = self.index
                events.append(json.dumps(event))
            return "\n".join(events), "application/json"
        if self.format == "elastic_bulk":
            lines = []
            for document in documents:
                lines.append(json.dumps({"index": {"_index": self.index, "_id": document["id"]}}))
                lines.append(json.dumps({**document, "@timestamp": document["at"]}))
            return "\n".join(lines) + "\n", "application/x-ndjson"
        return "\n".join(json.dumps(d) for d in documents) + "\n", "application/x-ndjson"

    async def export(self, entries: Sequence[AuditEntry]) -> None:
        if not entries:
            return
        content, content_type = self.body(entries)
        headers = {**self.headers, "Content-Type": content_type}
        try:
            async with outgoing_call(self.service, "export", method="POST", url=self.url) as call:
                response = await self.client.post(self.url, content=content, headers=headers)
                call.status = response.status_code
        except httpx.HTTPError as exc:
            raise transport_failed(exc, service=self.service, operation="export") from exc
        status = response.status_code
        if 400 <= status < 500 and status not in (408, 429):
            raise MessageRejected(
                f"The audit endpoint refused the entries: HTTP {status}",
                details={"status": status, "body": response.text[:500]},
            )
        check_response(response, service=self.service, operation="export")
        if self.format == "elastic_bulk":
            result = response.json()
            if result.get("errors"):
                failed = [
                    item.get("index", {}).get("error")
                    for item in result.get("items", [])
                    if item.get("index", {}).get("error")
                ]
                raise MessageRejected(
                    "Elasticsearch refused some audit entries",
                    details={"errors": failed[:5]},
                )


__all__ = ["HttpAuditExporter"]
