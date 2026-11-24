# resource-engine-opentelemetry

OpenTelemetry metrics for Resource Engine consumers. `ConsumerMetrics` is a
`MessageObserver`: it counts every message by consumer, outcome and event type,
and records how long each one took.

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
[docs/messaging.md](../../docs/messaging.md#metrics). Part of
[Resource Engine](../../README.md).
