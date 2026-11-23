"""Push endpoint for inbound events (webhooks).

Brokers that push over HTTP POST each message to this route: SAP Event Mesh
webhook subscriptions, SAP advanced event mesh REST delivery points, Pub/Sub push
subscriptions (with ``resource_engine_gcp.decode_pubsub_push``), Event Grid, or any
service sending the envelope JSON or CloudEvents.

Responses tell the broker what to do:

- ``204``: handled or duplicate; the broker must drop the message.
- ``503`` with ``Retry-After``: failed; the broker should redeliver.
- ``dead_letter_status`` (422 by default): it will never succeed (undecodable,
  no handler, ``MessageRejected``...). Brokers differ in what they do with 4xx,
  so set it to what yours dead-letters on.

Authenticate the caller with ``dependencies`` (e.g. a shared-secret or JWT check);
the engine's own inbound authorisation is still to come.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from resource_engine.messaging import MessageProcessor, Outcome, decode_http, try_decode
from resource_engine.ports.messaging import ReceivedMessage

type HttpDecoder = Callable[[Mapping[str, str], bytes], ReceivedMessage]


def default_decoder(type_prefix: str = "") -> HttpDecoder:
    """Envelope JSON or CloudEvents (structured or binary mode)."""
    return lambda headers, body: try_decode(
        lambda: decode_http(headers, body, type_prefix=type_prefix)
    )


def build_event_receiver(
    processor: MessageProcessor,
    *,
    path: str = "/events",
    decode: HttpDecoder | None = None,
    allowed_origins: Sequence[str] = (),
    dead_letter_status: int = 422,
    dependencies: Sequence[Any] | None = None,
    tags: list[str] | None = None,
) -> APIRouter:
    """Router with ``POST {path}`` feeding ``processor`` and the CloudEvents webhook
    validation handshake (``OPTIONS {path}``).

    Args:
        processor: Same ``MessageProcessor`` a pull consumer would use.
        decode: Request to ``ReceivedMessage``; defaults to ``default_decoder()``.
        allowed_origins: Origins accepted in the CloudEvents ``WebHook-Request-Origin``
            handshake (``"*"`` for any). Empty: the handshake is refused.
        dead_letter_status: Status for messages that must not be retried.
        dependencies: FastAPI dependencies run before the handler (authentication).
    """
    decoder = decode or default_decoder()
    router = APIRouter(tags=tags or ["events"], dependencies=list(dependencies or []))

    @router.post(path, status_code=204, summary=f"Receive events for {processor.name}")
    async def receive(request: Request) -> Response:
        message = decoder(request.headers, await request.body())
        result = await processor.process(message)
        if result.outcome.settled:
            return Response(status_code=204)
        status = 503 if result.outcome is Outcome.RETRY else dead_letter_status
        headers = {}
        if result.outcome is Outcome.RETRY:
            headers["Retry-After"] = str(max(1, math.ceil(result.delay_seconds)))
        return JSONResponse(
            status_code=status,
            headers=headers,
            content={"error": {"outcome": result.outcome.value, "message": result.error}},
        )

    @router.options(path, include_in_schema=False)
    async def handshake(request: Request) -> Response:
        origin = request.headers.get("webhook-request-origin")
        if origin and ("*" in allowed_origins or origin in allowed_origins):
            return Response(
                status_code=200,
                headers={"WebHook-Allowed-Origin": origin, "WebHook-Allowed-Rate": "*"},
            )
        return Response(status_code=403 if origin else 405)

    return router
