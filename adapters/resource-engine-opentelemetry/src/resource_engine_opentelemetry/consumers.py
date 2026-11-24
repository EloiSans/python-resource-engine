"""``MessageObserver`` recording consumer metrics with the OpenTelemetry API.

Instruments (meter ``resource_engine.messaging``):

- ``resource_engine.consumer.messages`` (counter, ``{message}``): every message
  a consumer settled, retried, dead-lettered or held back;
- ``resource_engine.consumer.duration`` (histogram, ``s``): time spent on each
  message the handler ran for. Held-back messages are not timed.

Attributes: ``resource_engine.consumer`` (the processor name),
``resource_engine.outcome`` (``ack``, ``duplicate``, ``retry``,
``dead_letter``), ``resource_engine.event_type`` (the envelope type, absent when
the body could not be decoded), and ``resource_engine.blocked`` on the counter.

Only the API is required. Without an SDK and exporter configured, the
instruments are no-ops.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics
from resource_engine.messaging import ProcessResult
from resource_engine.ports.messaging import ReceivedMessage

METER_NAME = "resource_engine.messaging"


class ConsumerMetrics:
    """Pass to ``MessageProcessor(observers=[ConsumerMetrics()])``.

    Args:
        meter_provider: Defaults to the global provider.
        include_event_type: Add the envelope type as an attribute. Turn it off
            when event types are many (cardinality).
    """

    def __init__(
        self, meter_provider: Any | None = None, *, include_event_type: bool = True
    ) -> None:
        from resource_engine import __version__

        meter = metrics.get_meter(METER_NAME, __version__, meter_provider=meter_provider)
        self.include_event_type = include_event_type
        self.messages = meter.create_counter(
            "resource_engine.consumer.messages",
            unit="{message}",
            description="Messages processed by Resource Engine consumers, by outcome.",
        )
        self.duration = meter.create_histogram(
            "resource_engine.consumer.duration",
            unit="s",
            description="Time spent processing one message (inbox, handler, commit).",
        )

    def __call__(
        self, consumer: str, message: ReceivedMessage, result: ProcessResult, seconds: float
    ) -> None:
        attributes: dict[str, str | bool] = {
            "resource_engine.consumer": consumer,
            "resource_engine.outcome": result.outcome.value,
        }
        if self.include_event_type and message.envelope is not None:
            attributes["resource_engine.event_type"] = message.envelope.type
        self.messages.add(1, {**attributes, "resource_engine.blocked": result.blocked})
        if not result.blocked:
            self.duration.record(seconds, attributes)
