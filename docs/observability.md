# Observability

The core emits traces, metrics hooks and logs through neutral ports.
`resource-engine-opentelemetry` connects them to OpenTelemetry.

| Signal | Core | OpenTelemetry |
|---|---|---|
| Traces | `Tracer` port, `set_tracer()` | `OtelTracer` |
| Consumer metrics | `MessageObserver`, `LoggingObserver` | `ConsumerMetrics` ([messaging](messaging.md#metrics)) |
| Job runs | `JobObserver` ([jobs](jobs.md#metrics)) | – |
| Logs | standard `logging`, `correlation_id` in messages | your log pipeline |

## Tracing

```python
from resource_engine.tracing import set_tracer
from resource_engine_opentelemetry import OtelTracer

set_tracer(OtelTracer())  # once, at start-up, after configuring the OpenTelemetry SDK
```

Without `set_tracer`, the core uses `NoopTracer`, which records nothing and
costs next to nothing.

### Spans

| Span | Kind | When |
|---|---|---|
| `<resource> <operation>` (`invoices create`, `invoices transition`…) | internal | Every manager operation: `list`, `get`, `create`, `update`, `delete`, `transition`, `action`; documents: `attach`, `detach`, `request_upload`, `confirm_upload`, `list_documents` |
| `outbox publish` | producer | Each batch the `OutboxRelay` sends |
| `<consumer> process` | consumer | Each message a `MessageProcessor` handles |
| `job <name>` | internal | Each `JobRunner` run |

Attributes:

- **Operations:** `resource_engine.resource`, `resource_engine.operation`,
  `resource_engine.action` (`transition:approve`, `action:send`),
  `resource_engine.entity_id`, `resource_engine.correlation_id`.
- **Consumers:** `resource_engine.consumer`, `resource_engine.event_type`,
  `messaging.message.id`, `messaging.message.delivery_count`,
  `resource_engine.outcome`.
- **Relay:** `messaging.batch.message_count`.
- **Jobs:** `resource_engine.job`.

Spans nest under the current span. With FastAPI and the OpenTelemetry FastAPI
instrumentation, an operation's span is a child of the request's span. An
operation called from a hook or a consumer handler is a child of that
operation or message.

**Errors:** an exception leaving an operation marks its span as failed. The
exception is recorded and `error.type` is set to its code (`RE-E702`, or the
code of your `DomainError`). A message that is retried or dead-lettered also
marks its span, with the error as the description.

### From the operation to the consumer

Every envelope created inside a span carries the trace context in `trace`
(W3C `traceparent` and `tracestate`):

- **Outbox and JSON:** it is stored with the outbox record and sent in the
  envelope JSON.
- **CloudEvents:** it becomes the `traceparent` and `tracestate` extensions.
- **Broker attributes:** it is also part of `attributes()`, so brokers can
  route or display it.

The consumer's span uses it as its parent, so one trace shows the request, the
operation, the event and every consumer that handled it. Envelopes from
producers without tracing have no `trace`, and their consumer spans start a
new trace.

**Propagators:** `OtelTracer` injects and extracts with the global propagator,
which is W3C trace context unless you configure another. Pass
`OtelTracer(propagator=...)` to choose one.

### Custom tracers

Implement the port (`resource_engine.ports.tracing.Tracer`): `span(name,
*, kind, attributes, parent)` as an async context manager, `current()` and
`inject()`. `resource_engine.testing.RecordingTracer` records spans in memory
for tests:

```python
tracer = RecordingTracer()
previous = set_tracer(tracer)
await manager.create("invoices", {...})
assert tracer.named("invoices create").attributes["resource_engine.operation"] == "create"
set_tracer(previous)
```
