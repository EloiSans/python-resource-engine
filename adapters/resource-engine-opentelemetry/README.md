# resource-engine-opentelemetry

OpenTelemetry for Resource Engine.

- `OtelTracer` traces operations, outbox publishing, consumers and jobs. The
  trace travels on event envelopes, so a consumer's span continues the trace
  of the operation that produced the event.
- `ConsumerMetrics` is a `MessageObserver`: it counts every message by
  consumer, outcome and event type, and records how long each one took.

```python
from resource_engine.tracing import set_tracer
from resource_engine_opentelemetry import OtelTracer

set_tracer(OtelTracer())
```

```python
from resource_engine.messaging import MessageProcessor
from resource_engine_opentelemetry import ConsumerMetrics

processor = MessageProcessor(router, name="billing", manager=manager, observers=[ConsumerMetrics()])
```

Instruments, on meter `resource_engine.messaging`:

| Name | Kind | Unit |
|---|---|---|
| `resource_engine.consumer.messages` | Counter | `{message}` |
| `resource_engine.consumer.duration` | Histogram | `s` |

The package depends on `opentelemetry-api` only. Configure the SDK and an
exporter in your application. See
[docs/observability.md](../../docs/observability.md). Part of
[Resource Engine](../../README.md).
