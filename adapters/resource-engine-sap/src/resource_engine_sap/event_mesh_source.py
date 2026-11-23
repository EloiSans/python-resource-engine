"""SAP Event Mesh queue as a ``MessageSource`` (REST pull).

Uses ``POST /messagingrest/v1/queues/{queue}/messages/consumption`` with
``x-qos: 1``; the response carries the message id in ``x-message-id`` and the
message stays on the queue until
``POST /messagingrest/v1/queues/{queue}/messages/{id}/acknowledgement``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from urllib.parse import quote

import httpx
from resource_engine.messaging import decode_http, try_decode
from resource_engine.ports.messaging import ReceivedMessage
from resource_engine_http.errors import check_response, transport_failed

log = logging.getLogger("resource_engine_sap")


class EventMeshMessageSource:
    """``MessageSource`` over an Event Mesh queue.

    Args:
        client: Client from ``event_mesh_client(service_key)``.
        queue: Queue name including its namespace (``acme/billing/1/invoices``).
        poll_interval: Seconds between polls while the queue is empty (the REST
            API has no long polling).
        type_prefix: CloudEvents type prefix to strip, if producers use one.

    Limitations of the REST API:

    - No negative acknowledgement: ``retry`` leaves the message unacknowledged
      and Event Mesh makes it available again after its own redelivery timeout,
      so the delay is not honoured.
    - No delivery count: configure the queue's dead-message queue
      (``maxRedeliveryCount``) to stop endless retries; ``MessageRejected`` and
      other permanent errors fall back to that as well.
    - One message per request: ``receive`` makes up to ``max_messages`` calls.

    Messages consumed through webhook subscriptions cannot be acknowledged with
    this API; for push delivery use ``build_event_receiver`` instead.
    """

    service = "sap-event-mesh"

    def __init__(
        self,
        client: httpx.AsyncClient,
        queue: str,
        *,
        poll_interval: float = 1.0,
        type_prefix: str = "",
    ) -> None:
        self.client = client
        self.queue = queue
        self.poll_interval = poll_interval
        self.type_prefix = type_prefix
        self._base = f"/messagingrest/v1/queues/{quote(queue, safe='')}/messages"

    async def _post(self, operation: str, url: str) -> httpx.Response:
        try:
            response = await self.client.post(url, headers={"x-qos": "1"})
        except httpx.HTTPError as exc:
            raise transport_failed(exc, service=self.service, operation=operation) from exc
        check_response(response, service=self.service, operation=operation)
        return response

    async def _consume_one(self) -> ReceivedMessage | None:
        response = await self._post("consume", f"{self._base}/consumption")
        if response.status_code == 204 or not response.content:
            return None
        message_id = response.headers.get("x-message-id")
        if message_id is None:
            log.warning("Event Mesh returned a message without x-message-id; it cannot be acked")
        return try_decode(
            lambda: decode_http(response.headers, response.content, type_prefix=self.type_prefix),
            handle=message_id,
            message_id=message_id,
        )

    async def receive(self, max_messages: int, wait_seconds: float) -> list[ReceivedMessage]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        messages: list[ReceivedMessage] = []
        while True:
            while len(messages) < max_messages:
                message = await self._consume_one()
                if message is None:
                    break
                messages.append(message)
            remaining = deadline - loop.time()
            if messages or remaining <= 0:
                return messages
            await asyncio.sleep(min(self.poll_interval, remaining))

    async def ack(self, messages: Sequence[ReceivedMessage]) -> None:
        for m in messages:
            if m.handle is not None:
                await self._post(
                    "acknowledge", f"{self._base}/{quote(m.handle, safe='')}/acknowledgement"
                )

    async def retry(self, messages: Sequence[ReceivedMessage], delay_seconds: float) -> None:
        log.debug("Leaving %d Event Mesh messages unacknowledged for redelivery", len(messages))
