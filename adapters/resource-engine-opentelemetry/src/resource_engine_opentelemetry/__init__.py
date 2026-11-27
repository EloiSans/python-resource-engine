"""OpenTelemetry for Resource Engine: tracing, operation, consumer and job
metrics, and audit entries as log records. See docs/observability.md."""

from resource_engine_opentelemetry.audit import OtelAuditExporter
from resource_engine_opentelemetry.consumers import ConsumerMetrics
from resource_engine_opentelemetry.operations import JobMetrics, OperationMetrics
from resource_engine_opentelemetry.tracing import OtelSpan, OtelTracer

__all__ = [
    "ConsumerMetrics",
    "JobMetrics",
    "OperationMetrics",
    "OtelAuditExporter",
    "OtelSpan",
    "OtelTracer",
]
