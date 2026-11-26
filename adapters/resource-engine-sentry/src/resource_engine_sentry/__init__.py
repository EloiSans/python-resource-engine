"""Sentry, GlitchTip and other Sentry-compatible error reporting.

```python
from resource_engine.reporting import set_reporter
from resource_engine_sentry import SentryReporter

set_reporter(SentryReporter(dsn=os.environ["SENTRY_DSN"], environment="production"))
```

Every failure the engine reports (``resource_engine.reporting``) becomes one
issue event:

- **Tags:** where it happened (``operation``, ``http``, ``consumer``,
  ``job``...), the engine's error code, resource, operation, consumer, event
  type, job, route, status and the remote service.
- **Context:** the full log context (``resource_engine``), including the
  correlation and request ids and the trace id when OpenTelemetry is on.
- **Grouping:** by error code, place and resource, so ``RE-E501`` from the
  billing API is one issue, not one per record.
- **User:** only the caller's id (``keycloak:ada``); the reporter adds no
  other personal data (the SDK's own data collection follows its options).

Expected errors (validation, not found, permissions, business rules) are never
sent. GlitchTip, Bugsink and self-hosted Sentry take the same DSN. See
docs/observability.md#error-reporting.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from resource_engine.reporting import ErrorReport, describe

TAGS = (
    "resource",
    "operation",
    "action",
    "consumer",
    "event_type",
    "job",
    "timer",
    "route",
    "method",
    "status",
    "outcome",
    "service",
)


class SentryReporter:
    """An ``ErrorReporter`` for Sentry-compatible services.

    Args:
        dsn: The project DSN. With a DSN the reporter initialises the SDK;
            without one it uses the SDK as the application set it up
            (``sentry_sdk.init(...)`` elsewhere, with its own integrations).
        environment / release: Passed to ``sentry_sdk.init``.
        tags: Fixed tags for every event (``{"service": "billing"}``).
        options: Any other ``sentry_sdk.init`` option (``traces_sample_rate``,
            ``transport``, ``integrations``...).
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        environment: str | None = None,
        release: str | None = None,
        tags: dict[str, str] | None = None,
        **options: Any,
    ) -> None:
        from sentry_sdk.integrations.logging import ignore_logger

        self.tags = dict(tags or {})
        # the default reporter logs every report: do not send it twice
        ignore_logger("resource_engine.errors")
        if dsn is not None:
            sentry_sdk.init(
                dsn=dsn,
                environment=environment,
                release=release,
                **options,
            )

    def report(self, report: ErrorReport) -> None:
        context = {k: _plain(v) for k, v in report.context.items()}
        with sentry_sdk.new_scope() as scope:
            scope.set_level(report.level)
            scope.set_tag("resource_engine.where", report.where)
            if report.code:
                scope.set_tag("resource_engine.code", report.code)
            for name in TAGS:
                if context.get(name) not in (None, ""):
                    scope.set_tag(f"resource_engine.{name}", str(context[name])[:200])
            for name, value in self.tags.items():
                scope.set_tag(name, value)
            trace_id = _trace_id()
            if trace_id:
                scope.set_tag("trace_id", trace_id)
            scope.set_context(
                "resource_engine",
                {"where": report.where, "code": report.code, "title": describe(report), **context},
            )
            if report.actor:
                scope.set_user({"id": report.actor})
            scope.fingerprint = [
                report.code or type(report.error).__name__,
                report.where,
                str(context.get("service") or context.get("resource") or context.get("job") or ""),
            ]
            sentry_sdk.capture_exception(report.error)


def _plain(value: Any) -> Any:
    return value if isinstance(value, str | int | float | bool) or value is None else str(value)


def _trace_id() -> str | None:
    from resource_engine.logs import _trace_ids

    return _trace_ids()[0]


__all__ = ["SentryReporter"]
