# resource-engine-opentelemetry

OpenTelemetry for Resource Engine.

- `OtelTracer` traces operations, outbox publishing, consumers and jobs. The
  trace travels on event envelopes, so a consumer's span continues the trace
  of the operation that produced the event.
- `ConsumerMetrics` is a `MessageObserver`: it counts every message by
  consumer, outcome and event type, and records how long each one took.
- `OperationMetrics` is an `OperationObserver`: it records how long every
  manager operation took, by resource, operation and outcome.
- `JobMetrics` is a `JobObserver`: it counts `JobRunner` runs by job and
  outcome, and records their duration.

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

```python
from resource_engine import ResourceManager
from resource_engine.jobs import JobRunner
from resource_engine_opentelemetry import JobMetrics, OperationMetrics

manager = ResourceManager(registry, uow, observers=[OperationMetrics()])
runner = JobRunner(locks=locks, observers=[JobMetrics()])
```

Instruments:

| Name | Kind | Unit | Meter |
|---|---|---|---|
| `resource_engine.operation.duration` | Histogram | `s` | `resource_engine` |
| `resource_engine.consumer.messages` | Counter | `{message}` | `resource_engine.messaging` |
| `resource_engine.consumer.duration` | Histogram | `s` | `resource_engine.messaging` |
| `resource_engine.job.runs` | Counter | `{run}` | `resource_engine.jobs` |
| `resource_engine.job.duration` | Histogram | `s` | `resource_engine.jobs` |

The package depends on `opentelemetry-api` only. Configure the SDK and an
exporter in your application. See
[docs/observability.md](../../docs/observability.md). Part of
[Resource Engine](../../README.md).
