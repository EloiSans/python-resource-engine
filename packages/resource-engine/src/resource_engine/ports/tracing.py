"""Tracing port: spans around operations, messages and jobs.

The core never imports a tracing library. It asks the configured ``Tracer``
(``resource_engine.tracing.set_tracer``) for spans, and puts the current trace
context on every event envelope so consumers continue the trace. The
OpenTelemetry implementation is ``resource_engine_opentelemetry.OtelTracer``.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Literal, Protocol

type SpanKind = Literal["internal", "producer", "consumer"]


class Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...

    def record_error(self, error: BaseException | str) -> None:
        """Mark the span as failed. Exceptions raised through ``Tracer.span``
        are recorded by the tracer itself."""
        ...


class Tracer(Protocol):
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: Mapping[str, str] | None = None,
    ) -> AbstractAsyncContextManager[Span]:
        """A child of the current span, or of ``parent`` (a carrier from
        ``inject``, e.g. an envelope's ``trace``). It is the current span inside
        the block; an exception leaving the block is recorded and re-raised."""
        ...

    def current(self) -> Span:
        """The current span (a no-op span when there is none)."""
        ...

    def inject(self) -> dict[str, str]:
        """The current trace context as a carrier (W3C ``traceparent`` and
        ``tracestate``); empty when there is no span."""
        ...
