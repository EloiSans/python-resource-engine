from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Literal

import httpx
from resource_engine.calls import outgoing_call
from resource_engine.errors import PublishFailed
from resource_engine.messaging import EventEnvelope
from resource_engine.messaging.cloudevents import CONTENT_TYPE, to_cloudevent_json

type WebhookFormat = Literal["envelope", "cloudevents"]


class WebhookMessagePublisher:
    """``MessagePublisher`` that POSTs each envelope to an HTTP endpoint.

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (base URL, auth, timeouts).
        url: Target URL, absolute or relative to the client's ``base_url``.
        format: ``"envelope"`` (the envelope JSON) or ``"cloudevents"``
            (structured-mode CloudEvents 1.0).
        source: CloudEvents ``source`` (required for ``"cloudevents"``).
        type_prefix: Prefix for the CloudEvents ``type`` (e.g. ``"com.acme.invoices."``).
        max_concurrency: Requests in flight at once.

    Every message also carries ``Idempotency-Key: <envelope id>`` and the envelope
    attributes as ``X-Event-*`` headers. Any 2xx counts as accepted; the rest are
    reported in ``PublishFailed``.
    """

    service = "webhook"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        format: WebhookFormat = "envelope",
        source: str | None = None,
        type_prefix: str = "",
        max_concurrency: int = 8,
    ) -> None:
        if format == "cloudevents" and not source:
            raise ValueError("format='cloudevents' needs a source, e.g. source='/invoices'")
        self.client = client
        self.url = url
        self.format = format
        self.source = source
        self.type_prefix = type_prefix
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # Extension points for broker-specific subclasses (SAP Event Mesh, AEM...).

    def url_for(self, env: EventEnvelope) -> str:
        return self.url

    def headers_for(self, env: EventEnvelope) -> dict[str, str]:
        headers = {"Idempotency-Key": env.id}
        headers.update(
            {f"X-Event-{k.replace('_', '-').title()}": v for k, v in env.attributes().items()}
        )
        return headers

    def _request(self, env: EventEnvelope) -> tuple[str, dict[str, str]]:
        headers = self.headers_for(env)
        if self.format == "cloudevents":
            headers["Content-Type"] = CONTENT_TYPE
            body = to_cloudevent_json(env, source=self.source or "", type_prefix=self.type_prefix)
            return body, headers
        headers["Content-Type"] = "application/json"
        return env.to_json(), headers

    async def _send(self, env: EventEnvelope) -> str | None:
        body, headers = self._request(env)
        url = self.url_for(env)
        async with self._semaphore:
            try:
                async with outgoing_call(self.service, "publish", method="POST", url=url) as call:
                    response = await self.client.post(url, content=body, headers=headers)
                    call.status = response.status_code
            except httpx.HTTPError as exc:
                return f"{type(exc).__name__}: {exc}"
        return None if response.is_success else f"HTTP {response.status_code}"

    async def publish(self, messages: Sequence[EventEnvelope]) -> None:
        results = await asyncio.gather(*(self._send(m) for m in messages))
        failed = [(m.id, err) for m, err in zip(messages, results, strict=True) if err]
        if failed:
            raise PublishFailed(
                f"{self.service} rejected {len(failed)} of {len(messages)} messages: "
                + "; ".join(err for _, err in failed[:3]),
                failed_ids=[i for i, _ in failed],
            )
