"""SAP Event Mesh (BTP ``enterprise-messaging`` service) over its REST API.

Messages go to ``POST {uri}/messagingrest/v1/topics/{topic}/messages`` (or
``.../queues/{queue}/messages``) with an ``x-qos`` header; the token comes from the
``httprest`` section of the service key (OAuth 2.0 client credentials).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import quote

import httpx
from resource_engine.messaging import EventEnvelope
from resource_engine_http import OAuth2ClientCredentials, WebhookMessagePublisher
from resource_engine_http.webhook import WebhookFormat

type Destination = str | Callable[[EventEnvelope], str]


def event_topic(namespace: str) -> Callable[[EventEnvelope], str]:
    """Topic per resource and event type: ``<namespace>/<resource>/<EventType>``.

    ``namespace`` is the instance namespace from the service key
    (``orgName/clientName/uniqueId``). Topic rules (length, allowed characters)
    are enforced by Event Mesh; keep resource names alphanumeric.
    """
    namespace = namespace.strip("/")
    return lambda env: f"{namespace}/{env.resource or 'events'}/{env.type}"


def httprest_credentials(service_key: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``messaging`` entry of a service key whose protocol is ``httprest``."""
    for entry in service_key.get("messaging", []):
        if "httprest" in entry.get("protocol", []):
            return entry
    raise ValueError("The service key has no 'httprest' messaging entry")


def event_mesh_client(service_key: Mapping[str, Any], **httpx_options: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` for the Event Mesh REST API from a service key.

    The caller owns the client (``async with`` or ``await client.aclose()``).
    Extra keyword arguments go to ``httpx.AsyncClient`` (timeouts, transport...).
    """
    entry = httprest_credentials(service_key)
    oa2 = entry["oa2"]
    auth = OAuth2ClientCredentials(oa2["tokenendpoint"], oa2["clientid"], oa2["clientsecret"])
    httpx_options.setdefault("timeout", 10.0)
    return httpx.AsyncClient(base_url=entry["uri"], auth=auth, **httpx_options)


class EventMeshMessagePublisher(WebhookMessagePublisher):
    """``MessagePublisher`` for SAP Event Mesh.

    Args:
        client: Client from ``event_mesh_client`` (or any client with the REST
            ``uri`` as ``base_url`` and a bearer token).
        topic: Topic name, or a function of the envelope (see ``event_topic``).
        queue: Publish straight to a queue instead of a topic.
        qos: ``1`` (default): Event Mesh persists the message before answering 204
            (at-least-once). ``0``: fire and forget.
        format: ``"cloudevents"`` (default, what SAP applications emit) or
            ``"envelope"``.
        source: CloudEvents ``source`` identifying this application
            (default ``/resource-engine``).
        type_prefix: CloudEvents type prefix, e.g. ``"sap.custom.invoices."``.

    One HTTP request per message (the API has no batch call), up to
    ``max_concurrency`` in flight.
    """

    service = "sap-event-mesh"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        topic: Destination | None = None,
        queue: Destination | None = None,
        qos: Literal[0, 1] = 1,
        format: WebhookFormat = "cloudevents",
        source: str = "/resource-engine",
        type_prefix: str = "",
        max_concurrency: int = 8,
    ) -> None:
        if (topic is None) == (queue is None):
            raise ValueError("Pass exactly one of topic= or queue=")
        super().__init__(
            client,
            "",
            format=format,
            source=source,
            type_prefix=type_prefix,
            max_concurrency=max_concurrency,
        )
        self.kind = "topics" if topic is not None else "queues"
        self.destination: Destination = topic if topic is not None else queue  # type: ignore[assignment]
        self.qos = qos

    def url_for(self, env: EventEnvelope) -> str:
        name = self.destination(env) if callable(self.destination) else self.destination
        return f"/messagingrest/v1/{self.kind}/{quote(name, safe='')}/messages"

    def headers_for(self, env: EventEnvelope) -> dict[str, str]:
        return {**super().headers_for(env), "x-qos": str(self.qos)}
