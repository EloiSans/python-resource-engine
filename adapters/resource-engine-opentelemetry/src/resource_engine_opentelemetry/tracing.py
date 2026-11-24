"""``Tracer`` on the OpenTelemetry API.

```python
from resource_engine.tracing import set_tracer
from resource_engine_opentelemetry import OtelTracer

set_tracer(OtelTracer())
```

Spans come from the tracer ``resource_engine``. They nest under whatever span
is current (a FastAPI request instrumented by OpenTelemetry, for example). The
trace context goes on event envelopes through the configured propagator (W3C
``traceparent`` by default), and consumer spans continue it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.propagators.textmap import TextMapPropagator
from opentelemetry.trace import SpanKind, Status, StatusCode

KINDS = {
    "internal": SpanKind.INTERNAL,
    "producer": SpanKind.PRODUCER,
    "consumer": SpanKind.CONSUMER,
}


class OtelSpan:
    def __init__(self, span: trace.Span) -> None:
        self.span = span

    def set_attribute(self, key: str, value: Any) -> None:
        if value is not None:
            self.span.set_attribute(key, value)

    def record_error(self, error: BaseException | str) -> None:
        if isinstance(error, BaseException):
            self.span.record_exception(error)
            self.span.set_attribute(
                "error.type", getattr(error, "code", None) or type(error).__qualname__
            )
            description = str(error).splitlines()[0] if str(error) else type(error).__name__
        else:
            description = error
        self.span.set_status(Status(StatusCode.ERROR, description))


class OtelTracer:
    """Args:
    tracer_provider: Defaults to the global provider.
    propagator: Defaults to the global propagator (W3C trace context and
        baggage unless configured otherwise).
    """

    def __init__(
        self,
        tracer_provider: trace.TracerProvider | None = None,
        *,
        propagator: TextMapPropagator | None = None,
    ) -> None:
        from resource_engine import __version__

        self.tracer = trace.get_tracer("resource_engine", __version__, tracer_provider)
        self.propagator = propagator

    @asynccontextmanager
    async def span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: Mapping[str, str] | None = None,
    ) -> AsyncIterator[OtelSpan]:
        context = self._extract(parent) if parent else None
        clean = {k: v for k, v in (attributes or {}).items() if v is not None}
        with self.tracer.start_as_current_span(
            name,
            context=context,
            kind=KINDS.get(kind, SpanKind.INTERNAL),
            attributes=clean,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            wrapped = OtelSpan(span)
            try:
                yield wrapped
            except Exception as exc:
                wrapped.record_error(exc)
                raise

    def current(self) -> OtelSpan:
        return OtelSpan(trace.get_current_span())

    def inject(self) -> dict[str, str]:
        carrier: dict[str, str] = {}
        if self.propagator is not None:
            self.propagator.inject(carrier)
        else:
            propagate.inject(carrier)
        return carrier

    def _extract(self, carrier: Mapping[str, str]) -> Any:
        if self.propagator is not None:
            return self.propagator.extract(dict(carrier))
        return propagate.extract(dict(carrier))
