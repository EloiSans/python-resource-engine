"""Azure Event Grid as a ``MessagePublisher`` (extra ``[eventgrid]``): events
posted to a topic as CloudEvents 1.0.

```python
publisher = EventGridPublisher(
    http, "https://invoices.westeurope-1.eventgrid.azure.net/api/events",
    key=TOPIC_KEY, source="/invoices",
)
```

The topic must use the CloudEvents schema. Authentication is the topic's
access key (``aeg-sas-key``) or a Microsoft Entra token (``token=``: an async
function returning one, such as ``azure.identity`` for the
``https://eventgrid.azure.net/.default`` scope). Batches stay under the 1 MB
limit; a refused batch is reported in ``PublishFailed``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from resource_engine.calls import outgoing_call
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope
from resource_engine.messaging.cloudevents import to_cloudevent

MAX_BYTES = 1_000_000


class EventGridPublisher:
    """``MessagePublisher`` posting CloudEvents batches to an Event Grid topic.

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        endpoint: The topic endpoint (``https://<topic>.<region>.eventgrid.azure.net/api/events``).
        source: The CloudEvents ``source``.
        key: The topic's access key; or ``token`` for Entra ID.
        type_prefix: Prefix of the CloudEvents ``type`` (``com.example.invoices.``).
    """

    service = "eventgrid"

    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        *,
        source: str,
        key: str | None = None,
        token: Callable[[], Awaitable[str]] | None = None,
        type_prefix: str = "",
    ) -> None:
        if not (key or token):
            raise ValueError("Pass the topic key or a token function")
        self.client = client
        self.endpoint = endpoint
        self.source = source
        self.key = key
        self.token = token
        self.type_prefix = type_prefix

    async def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/cloudevents-batch+json; charset=utf-8"}
        if self.key:
            headers["aeg-sas-key"] = self.key
        else:
            headers["Authorization"] = f"Bearer {await self.token()}"  # type: ignore[misc]
        return headers

    def batches(self, messages: Sequence[EventEnvelope]) -> list[list[tuple[str, dict[str, Any]]]]:
        out: list[list[tuple[str, dict[str, Any]]]] = []
        current: list[tuple[str, dict[str, Any]]] = []
        size = 2
        for envelope in messages:
            event = to_cloudevent(envelope, source=self.source, type_prefix=self.type_prefix)
            length = len(json.dumps(event).encode()) + 1
            if current and size + length > MAX_BYTES:
                out.append(current)
                current, size = [], 2
            current.append((envelope.id, event))
            size += length
        if current:
            out.append(current)
        return out

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        failed: list[str] = []
        errors: list[str] = []
        for batch in self.batches(messages):
            body = json.dumps([event for _, event in batch])
            try:
                async with outgoing_call(
                    self.service, "publish", method="POST", url=self.endpoint
                ) as call:
                    response = await self.client.post(
                        self.endpoint, content=body, headers=await self._headers()
                    )
                    call.status = response.status_code
                ok = response.is_success
                reason = f"HTTP {response.status_code} {response.text[:200]}"
            except httpx.HTTPError as exc:
                ok, reason = False, f"{type(exc).__name__}: {exc}"
            if not ok:
                failed += [id_ for id_, _ in batch]
                errors.append(reason)
        if failed:
            raise PublishFailed(
                f"Event Grid refused {len(failed)} of {len(messages)} events: "
                f"{'; '.join(errors[:3])}",
                failed_ids=failed,
            )


__all__ = ["EventGridPublisher"]
