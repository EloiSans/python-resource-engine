"""SAP Integration Suite, advanced event mesh (Solace PubSub+) over Solace REST messaging.

Messages go to ``POST {broker}/TOPIC/{topic}`` on the broker's REST port (usually
9443). Solace-specific headers carry the message id, correlation id, delivery mode
and user properties.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from urllib.parse import quote

import httpx
from resource_engine.messaging import EventEnvelope
from resource_engine_http import WebhookMessagePublisher
from resource_engine_http.webhook import WebhookFormat

type DeliveryMode = Literal["Persistent", "Non-Persistent", "Direct"]
type Topic = str | Callable[[EventEnvelope], str]


def event_topic(root: str) -> Callable[[EventEnvelope], str]:
    """Hierarchical topic ``<root>/<resource>/<EventType>/<entity key>``.

    Solace subscriptions use ``/`` levels with ``*`` and ``>`` wildcards, so
    consumers can subscribe to ``<root>/invoices/>`` or ``<root>/*/InvoiceApproved/>``.
    """
    root = root.strip("/")

    def topic(env: EventEnvelope) -> str:
        entity = (env.key or "").split(":", 1)[-1] or "-"
        return f"{root}/{env.resource or 'events'}/{env.type}/{entity}"

    return topic


def _solace_string(value: str) -> str:
    return quote(value, safe="")


class AdvancedEventMeshPublisher(WebhookMessagePublisher):
    """``MessagePublisher`` for SAP advanced event mesh / Solace brokers.

    Args:
        client: ``httpx.AsyncClient`` with the broker REST URL as ``base_url`` and
            its auth (``httpx.BasicAuth`` for client usernames, or
            ``OAuth2ClientCredentials``).
        topic: Topic, or a function of the envelope (see ``event_topic``).
        delivery_mode: ``Persistent`` (default; the broker answers only after
            spooling the message, so delivery is at-least-once), ``Non-Persistent``
            or ``Direct``.
        time_to_live_ms: Optional message TTL.
        partition_key: Send the envelope key as ``JMSXGroupID`` so partitioned
            queues keep per-entity order.
        format: ``"envelope"`` (default) or ``"cloudevents"``.

    The envelope attributes travel as ``Solace-User-Property-<name>`` headers, so
    they are user properties on the consumer side.
    """

    service = "sap-advanced-event-mesh"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        topic: Topic,
        delivery_mode: DeliveryMode = "Persistent",
        time_to_live_ms: int | None = None,
        partition_key: bool = True,
        format: WebhookFormat = "envelope",
        source: str | None = None,
        type_prefix: str = "",
        max_concurrency: int = 8,
    ) -> None:
        super().__init__(
            client,
            "",
            format=format,
            source=source,
            type_prefix=type_prefix,
            max_concurrency=max_concurrency,
        )
        self.topic = topic
        self.delivery_mode = delivery_mode
        self.time_to_live_ms = time_to_live_ms
        self.partition_key = partition_key

    def url_for(self, env: EventEnvelope) -> str:
        topic = self.topic(env) if callable(self.topic) else self.topic
        return "/TOPIC/" + quote(topic, safe="/")

    def headers_for(self, env: EventEnvelope) -> dict[str, str]:
        headers = {
            "Solace-Message-ID": _solace_string(env.id),
            "Solace-Delivery-Mode": self.delivery_mode,
        }
        if env.correlation_id:
            headers["Solace-Correlation-ID"] = _solace_string(env.correlation_id)
        if self.time_to_live_ms is not None:
            headers["Solace-Time-To-Live-In-ms"] = str(self.time_to_live_ms)
        for name, value in env.attributes().items():
            headers[f"Solace-User-Property-{name}"] = _solace_string(value)
        if self.partition_key and env.key:
            headers["Solace-User-Property-JMSXGroupID"] = _solace_string(env.key)
        return headers
