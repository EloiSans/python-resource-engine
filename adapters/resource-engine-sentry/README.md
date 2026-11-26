# resource-engine-sentry

Error reporting for [Resource Engine](https://github.com/EloiSans/python-resource-engine)
to Sentry and Sentry-compatible services (GlitchTip, Bugsink, self-hosted Sentry).

```python
from resource_engine.reporting import set_reporter
from resource_engine_sentry import SentryReporter

set_reporter(SentryReporter(dsn="https://key@glitchtip.example.com/1", environment="production"))
```

- **Scope:** every unexpected failure the engine reports becomes one event:
  operations, HTTP, GraphQL and MCP 5xx answers, consumer dead letters,
  outbox, jobs, timers and work after commit.
- **What an event carries:**
  - the engine's error code and context as tags (resource, operation, route,
    consumer, job, remote service, trace id);
  - grouping by code, place and resource;
  - only the caller's id as user.
- **Not sent:** expected errors (validation, not found, permissions, business
  rules).

See [docs/observability.md](https://github.com/EloiSans/python-resource-engine/blob/main/docs/observability.md#error-reporting).
