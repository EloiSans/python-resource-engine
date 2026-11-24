"""OpenTelemetry for Resource Engine: consumer metrics and tracing.
See docs/observability.md."""

from resource_engine_opentelemetry.consumers import ConsumerMetrics
from resource_engine_opentelemetry.tracing import OtelSpan, OtelTracer

__all__ = ["ConsumerMetrics", "OtelSpan", "OtelTracer"]
