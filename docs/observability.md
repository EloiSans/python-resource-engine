# Observability

The core emits traces, metrics hooks and logs through neutral ports.
`resource-engine-opentelemetry` connects them to OpenTelemetry.

| Signal | Core | OpenTelemetry |
|---|---|---|
| Traces | `Tracer` port, `set_tracer()` | `OtelTracer` |
| Operation metrics | `OperationObserver` (`ResourceManager(observers=...)`) | `OperationMetrics` |
| Consumer metrics | `MessageObserver`, `LoggingObserver` | `ConsumerMetrics` ([messaging](messaging.md#metrics)) |
| Job runs | `JobObserver` ([jobs](jobs.md#metrics)) | `JobMetrics` |
| Logs | standard `logging`, log context, `JsonFormatter` | your log pipeline |
| Errors | `ErrorReporter` port, `set_reporter()`; logged by default | `resource-engine-sentry`: Sentry, GlitchTip, Bugsink ([below](#error-reporting)) |
| HTTP access | `AccessLogMiddleware` (`mount_resources(access_log=True)`), request ids | your log pipeline |
| Outgoing calls | `outgoing_call()` in the REST, SOAP and webhook adapters | client spans |

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
| `<resource> <operation>` (`invoices create`, `invoices transition`…) | internal | Every manager operation: `list`, `get`, `create`, `update`, `delete`, `transition`, `action`, `history`; documents: `attach`, `detach`, `request_upload`, `confirm_upload`, `list_documents` |
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

## Metrics

The core calls observers; it records nothing itself. Each observer is a plain
callable, so a Prometheus or StatsD adapter is a few lines. An observer that
raises is logged and ignored: it never changes the outcome.

```python
from resource_engine_opentelemetry import ConsumerMetrics, JobMetrics, OperationMetrics

manager = ResourceManager(registry, uow, observers=[OperationMetrics()])
processor = MessageProcessor(router, name="billing", manager=manager, observers=[ConsumerMetrics()])
runner = JobRunner(locks=locks, observers=[JobMetrics()])
```

### Operations

`ResourceManager(observers=[...])` calls each observer with an
`OperationRun` (`resource_engine.ports.observability`) after every operation,
the document operations included:

| Field | |
|---|---|
| `resource`, `operation` | `invoices`, `transition` |
| `outcome` | `ok` or `failed` |
| `seconds` | From the call to its return, post-commit work included |
| `action` | `transition:approve`, `action:send`, or `None` |
| `error_type` | The error code (`RE-E702`, your `DomainError` code) or class name |
| `correlation_id` | The operation's; `None` if it failed before it had one |
| `error` | The exception, when failed |

An operation called from a hook is observed on its own, and its time is also
part of the outer operation's.

### Instruments

| Name | Kind | Unit | Attributes |
|---|---|---|---|
| `resource_engine.operation.duration` | Histogram | `s` | `resource_engine.resource`, `resource_engine.operation`, `resource_engine.outcome`, `error.type`, `resource_engine.action` |
| `resource_engine.consumer.messages` | Counter | `{message}` | `resource_engine.consumer`, `resource_engine.outcome`, `resource_engine.event_type`, `resource_engine.blocked` |
| `resource_engine.consumer.duration` | Histogram | `s` | as above, without `blocked` |
| `resource_engine.job.runs` | Counter | `{run}` | `resource_engine.job`, `resource_engine.outcome` (`ok`, `failed`, `skipped`), `error.type` |
| `resource_engine.job.duration` | Histogram | `s` | as above; skipped runs are not timed |

The histogram's count is the number of operations, so request rates and error
rates come from it too. Turn off `include_action` (operations) or
`include_event_type` (consumers) when those values are many.

## Logs

The engine logs through the standard `logging` module, under the
`resource_engine` logger and its children. While it works, it keeps a *log
context* that every record can carry:

| Where | Fields |
|---|---|
| A manager operation | `resource`, `operation`, `correlation_id`, `entity_id`, `action` |
| A consumer handling a message | `consumer`, `message_id`, `event_type`, `correlation_id` (the envelope's) |
| A `JobRunner` run | `job` |

Contexts nest: an operation a handler calls keeps `consumer` and
`message_id`, with its own `correlation_id`. Your own log calls in hooks,
actions and handlers carry the context too.

```python
from resource_engine.logs import configure_logging

configure_logging(format="json", static={"service": "billing"})
```

`configure_logging` replaces the root logger's handlers with one stream handler
(stderr by default). `json` writes one object per line:

```json
{"timestamp": "2026-11-25T10:02:11.481+00:00", "level": "ERROR",
 "logger": "resource_engine", "message": "after_commit hook 'notify' failed ...",
 "service": "billing", "correlation_id": "6f1c...", "resource": "invoices",
 "operation": "transition", "entity_id": "7", "action": "transition:approve",
 "trace_id": "4bf92f35...", "span_id": "00f067aa...",
 "exception": "Traceback ...", "error_type": "RuntimeError"}
```

- Fields that are not set are left out.
- `extra=` fields are added as they are.
- `trace_id` and `span_id` come from the current span, when a tracer is
  configured, so log lines link to traces.
- `text` writes `time level logger [correlation_id] message`.

**Your own logging setup:** add `LogContextFilter` to your handler. It puts the
fields on each record (`%(correlation_id)s` in a text format), and you can use
`JsonFormatter` with any handler. `resource-engine worker --log-format json`
does this for the worker.

**Your own fields:** `bind_log_context(tenant="acme")` adds fields inside a
`with` block, and `log_context()` returns the current ones.

## Error reporting

Every unexpected failure goes to the configured error reporters once, wherever
it happens:

| `where` | Reported |
|---|---|
| `operation` | A manager operation failed with something other than a client or business error |
| `http`, `graphql`, `mcp` | A 5xx answer: an engine failure (`RE-E501`, storage, configuration) or any other exception (answered as 500 `RE-E000` "Internal error") |
| `consumer` | A message handler failed (error), or a message was dead-lettered (error, or warning when its cause is a business error) |
| `outbox` | A batch could not be published |
| `job`, `timer` | A job run or a timed transition failed |
| `after_commit`, `on_commit_callback`, `rollback_callback` | Work after the commit, or a compensation, failed (the operation itself succeeded or was already rolled back) |

- **Expected errors are not reported.** Validation, not found, permission,
  tenancy, version conflicts, idempotency and business rules (`DomainError`)
  are answers to the caller, not failures.
- **Once per failure.** An exception reported by the operation is not reported
  again by the HTTP adapter, the consumer or a doomed transaction.
- **Context.** Each report carries:
  - the log context: request id, correlation id, resource, operation, record,
    consumer, message, job;
  - details of where it happened: route, method, status, remote service;
  - the error's engine code;
  - the caller's id.

The default reporter logs at `ERROR` (or `WARNING`) on
`resource_engine.errors`, with the traceback and the context as fields, so the
JSON logs already hold every failure. Add a reporter with `set_reporter`:

```python
from resource_engine.reporting import set_reporter
from resource_engine_sentry import SentryReporter

set_reporter(
    SentryReporter(dsn=os.environ["SENTRY_DSN"], environment="production", release=VERSION)
)
```

### Sentry, GlitchTip and Bugsink

`resource-engine-sentry` sends reports through the Sentry SDK. Every service
that speaks the Sentry protocol takes the same DSN: Sentry (cloud or
self-hosted), GlitchTip and Bugsink.

- **Tags:** `resource_engine.where`, `resource_engine.code`, the resource,
  operation, consumer, event type, job, route, status and remote service;
  `trace_id` when OpenTelemetry is on; and your own fixed tags
  (`tags={"service": "billing"}`).
- **Context:** the full context under `resource_engine`.
- **Grouping:** by code, place and resource or service. `RE-E501` from the
  billing API is one issue, whatever record triggered it, and HTTP routes
  appear as templates (`/invoices/{entity_id}`), never with ids.
- **User:** only the caller's id. The reporter adds no other personal data.
- **No duplicates:** Sentry's logging integration ignores
  `resource_engine.errors`, so the default log line and the explicit report
  don't become two events.
- **Without `dsn=`:** the reporter uses an SDK your application initialised
  itself, with its own integrations (FastAPI, SQLAlchemy, httpx…).

To use any other tool (Rollbar, Honeybadger, a database table), implement
`report(report: ErrorReport)`, sync or async.

## Access log and request ids

`mount_resources(app, ..., access_log=True)` (or `install_access_log(app)`)
writes one line per request on `resource_engine.access`:

```json
{"message": "GET /invoices/{entity_id} 200 12.4 ms", "http_method": "GET", "http_route": "/invoices/{entity_id}",
 "http_status": 200, "duration_ms": 12.4, "principal": "keycloak:ada", "request_id": "5f0c…"}
```

- **Request ids:** each request gets one from `X-Request-Id`, or a new one,
  and it is returned in the response. It is in the log context of everything
  the request does (operation logs, outgoing calls, error reports), so one id
  finds all of it.
- **Routes:** they are logged as templates.
- **What's covered:** GraphQL and MCP requests mounted on the same app are
  covered too.
- **Skipped:** `/health`, `/healthz` and `/ready` aren't logged.
- **Levels:** 5xx lines are warnings; the failure itself is reported
  separately.

## Outgoing calls

The REST and SOAP repositories and the webhook publisher wrap every call in
`outgoing_call(service, operation)`, which gives a client span and a line on
`resource_engine.outgoing`:

```json
{"message": "billing-api get 503 230.1 ms", "remote_service": "billing-api", "remote_operation": "get",
 "http_method": "GET", "http_status": 503, "url": "https://billing.example.com/invoices/7",
 "duration_ms": 230.1, "outcome": "failed", "request_id": "5f0c…", "correlation_id": "…"}
```

- **Levels:** INFO, or WARNING when the call failed.
- **URL:** scheme, host and path only; no query string or credentials.
- **Your own adapters:** use `resource_engine.calls.outgoing_call` the same
  way.
