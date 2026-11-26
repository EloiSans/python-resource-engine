"""Error reporting: every unexpected failure reaches one ``ErrorReporter``.

```python
from resource_engine.reporting import set_reporter
from resource_engine_sentry import SentryReporter

set_reporter(SentryReporter(dsn="https://key@glitchtip.example.com/1"))
```

The engine reports, once per failure:

- **operations** that fail with something other than a client or business
  error (``where="operation"``);
- **5xx responses** of the HTTP, GraphQL and MCP adapters (``http``,
  ``graphql``, ``mcp``);
- **messages** a consumer dead-letters or fails on (``consumer``), and
  **outbox** batches that cannot be published (``outbox``);
- **jobs**, **timers** and **processes** that fail (``job``, ``timer``,
  ``process``), and work after commit (``after_commit``).

Expected errors (validation, not found, permission, conflicts, business rules)
are not reported: they are answers, not failures. Each report carries the log
context (correlation id, resource, operation, record, consumer, job...) and
the error's engine code. The default reporter logs at ``ERROR`` on
``resource_engine.errors``. See docs/observability.md#error-reporting.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from resource_engine.logs import error_type, log_context

logger = logging.getLogger("resource_engine.errors")

type Level = Literal["error", "warning"]

_MARK = "_resource_engine_reported"


@dataclass(frozen=True)
class ErrorReport:
    """One failure.

    Attributes:
        error: The exception.
        where: The part of the engine it happened in (``operation``, ``http``...).
        level: ``error``, or ``warning`` for expected errors that still matter
            (a message dead-lettered because its payload is invalid).
        code: The engine's code (``RE-E501``), a ``DomainError`` code, or the
            exception's class name.
        context: The log context and the call site's details (``route``,
            ``status``, ``service``...).
        actor: The caller's id (``keycloak:ada``), when known.
    """

    error: BaseException
    where: str
    level: Level = "error"
    code: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    actor: str | None = None


class ErrorReporter(Protocol):
    """Receives every reported failure (sync or async)."""

    def report(self, report: ErrorReport) -> Any: ...


class LoggingReporter:
    """Logs reports on ``resource_engine.errors`` with the traceback."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        self.logger = log or logger

    def report(self, report: ErrorReport) -> None:
        level = logging.ERROR if report.level == "error" else logging.WARNING
        self.logger.log(
            level,
            "%s: %s: %s",
            describe(report),
            report.code,
            report.error,
            exc_info=(type(report.error), report.error, report.error.__traceback__),
            extra={
                "error_where": report.where,
                "error_code": report.code,
                **({"actor": report.actor} if report.actor else {}),
                **{f"error_{k}": v for k, v in report.context.items() if k not in _LOGGED},
            },
        )


def describe(report: ErrorReport) -> str:
    """A short title for a report (log message, issue title)."""
    c = report.context
    where = report.where
    if where == "job":
        return f"Job {c.get('job')} failed"
    if c.get("hook"):
        return f"{where} hook {c['hook']} failed for {c.get('resource')}"
    if c.get("callback"):
        return f"{where} {c['callback']} failed for {c.get('resource')}"
    if where == "timer":
        record = f"{c.get('resource')} {c.get('entity_id')!r}"
        return f"Timed transition {c.get('timer')} failed for {record}"
    if where == "consumer":
        return (
            f"Message {c.get('message_id')} ({c.get('event_type')}) failed in {c.get('consumer')}"
        )
    if where == "operation":
        return f"{c.get('resource')} {c.get('operation')} failed"
    if where in ("http", "graphql", "mcp") and c.get("route"):
        return f"{c.get('method', where.upper())} {c['route']} failed"
    if where == "outbox":
        return "Publishing outbox messages failed"
    return f"{where} failed"


# Fields the log context already carries onto every record.
_LOGGED = {
    "correlation_id",
    "resource",
    "operation",
    "entity_id",
    "action",
    "consumer",
    "message_id",
    "event_type",
    "job",
    "request_id",
}

_reporters: list[Any] = [LoggingReporter()]


def set_reporter(*reporters: Any, logging_too: bool = True) -> None:
    """Send reports to ``reporters`` (and, with ``logging_too``, to the log)."""
    _reporters[:] = [*([LoggingReporter()] if logging_too else []), *reporters]


def get_reporters() -> list[Any]:
    return list(_reporters)


def is_expected(error: BaseException) -> bool:
    """Client and business errors: answers to the caller, not failures."""
    from resource_engine import errors as e
    from resource_engine.domain import DomainError

    if isinstance(error, DomainError):
        return True
    if isinstance(error, e.TransactionDoomed):
        cause = error.__cause__
        return cause is not None and is_expected(cause)
    unexpected = (
        e.ConfigurationError,
        e.RemoteCallFailed,
        e.StorageFailed,
        e.PublishFailed,
        e.ConditionNotSupported,
    )
    if isinstance(error, unexpected):
        return False
    return isinstance(error, e.ResourceEngineError)


async def report_error(
    error: BaseException,
    *,
    where: str,
    level: Level | None = None,
    actor: Any = None,
    force: bool = False,
    **context: Any,
) -> None:
    """Report ``error`` once (later calls for the same exception are ignored).
    Expected errors are skipped unless ``force``. Never raises."""
    if getattr(error, _MARK, False):
        return
    cause = error.__cause__
    if cause is not None and getattr(cause, _MARK, False):
        return  # the original failure was reported (a doomed transaction, a wrapper)
    if not force and is_expected(error):
        return
    with contextlib.suppress(Exception):  # some exceptions forbid attributes
        setattr(error, _MARK, True)
    report = ErrorReport(
        error=error,
        where=where,
        level=level or ("warning" if is_expected(error) else "error"),
        code=error_type(error),
        context={**log_context(), **{k: v for k, v in context.items() if v is not None}},
        actor=getattr(actor, "id", None) or (str(actor) if isinstance(actor, str) else None),
    )
    for reporter in list(_reporters):
        try:
            result = reporter.report(report)
            if inspect.isawaitable(result):
                await result
        except Exception:  # a broken reporter must not hide the original failure
            logger.exception("Error reporter %r failed", reporter)


def was_reported(error: BaseException) -> bool:
    return bool(getattr(error, _MARK, False))


__all__ = [
    "ErrorReport",
    "ErrorReporter",
    "LoggingReporter",
    "describe",
    "get_reporters",
    "is_expected",
    "report_error",
    "set_reporter",
    "was_reported",
]
