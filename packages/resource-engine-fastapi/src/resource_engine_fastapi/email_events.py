"""An endpoint for mail services' webhooks: bounces, complaints, deliveries.

```python
from resource_engine.email import EmailEventHandler
from resource_engine_fastapi import mount_email_events
from resource_engine_http import parse_postmark

mount_email_events(
    app, "/webhooks/postmark", parse_postmark, EmailEventHandler(suppressions), token=SECRET
)
```

Configure the provider to call ``https://<app>/webhooks/postmark?token=<SECRET>``
(or with HTTP Basic, password ``<SECRET>``): requests without the token are
refused (401) before anything is parsed. Answers 204; a payload the parser
doesn't recognise gives no events and is still acknowledged, so the provider
doesn't retry it forever.
"""

from __future__ import annotations

import base64
import hmac
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response


def _token_of(request: Request) -> str | None:
    if "token" in request.query_params:
        return request.query_params["token"]
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    if scheme.lower() == "basic":
        try:
            return base64.b64decode(value).decode().partition(":")[2]
        except (ValueError, UnicodeDecodeError):
            return None
    return None


def mount_email_events(
    app: Any,
    path: str,
    parser: Callable[[Any], list[Any]],
    handler: Any,
    *,
    token: str | None,
) -> None:
    """``POST path``: ``parser(json)`` → ``EmailEvent``s → ``handler.handle``.

    ``token``: the shared secret the provider sends (query ``token``, a bearer
    token, or the Basic password). ``None`` accepts anyone: only behind a
    gateway that authenticates the provider.
    """

    async def receive(request: Request) -> Response:
        if token is not None and not hmac.compare_digest(_token_of(request) or "", token):
            return Response(status_code=401)
        try:
            payload = await request.json()
        except ValueError:
            return Response(status_code=400)
        events = parser(payload)
        if events:
            await handler.handle(events)
        return Response(status_code=204)

    app.add_api_route(
        path,
        receive,
        methods=["POST"],
        include_in_schema=False,
        name=f"email-events:{path}",
    )


__all__ = ["mount_email_events"]
