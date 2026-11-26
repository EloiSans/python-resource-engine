"""Structured logs: the operation, message or job a log line belongs to, and
JSON output. See docs/observability.md#logs.

```python
from resource_engine.logs import configure_logging

configure_logging(format="json")  # one JSON object per line on stderr
```

The engine keeps a *log context* while it works: ``resource``, ``operation``,
``correlation_id``, ``entity_id`` and ``action`` inside a manager operation;
``consumer``, ``message_id``, ``event_type`` and the envelope's
``correlation_id`` while a consumer handles a message; ``job`` during a
``JobRunner`` run. ``LogContextFilter`` copies it onto every record, so your own
``logging`` calls inside hooks and handlers carry it too. ``JsonFormatter`` adds
the trace and span ids of the current span when a tracer is configured.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal, TextIO

#: Fields the engine puts in the log context, in output order.
CONTEXT_FIELDS = (
    "request_id",
    "correlation_id",
    "resource",
    "operation",
    "entity_id",
    "action",
    "consumer",
    "message_id",
    "event_type",
    "job",
)

_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "resource_engine_log_context", default=None
)

# Attributes every LogRecord has; anything else came from ``extra=``.
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def log_context() -> dict[str, Any]:
    """The current log context (a copy)."""
    return dict(_context.get() or {})


@contextmanager
def bind_log_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Add ``fields`` to the log context inside the block. Nested blocks see
    the outer fields too; ``None`` values are left out. Yields the new context,
    which the engine updates in place once it knows more (the correlation id)."""
    current = {**(_context.get() or {}), **{k: v for k, v in fields.items() if v is not None}}
    token = _context.set(current)
    try:
        yield current
    finally:
        _context.reset(token)


def update_log_context(**fields: Any) -> None:
    """Set ``fields`` in the innermost ``bind_log_context`` block (no-op outside one)."""
    current = _context.get()
    if current is not None:
        current.update({k: v for k, v in fields.items() if v is not None})


def _trace_ids() -> tuple[str | None, str | None]:
    from resource_engine.tracing import get_tracer

    parent = get_tracer().inject().get("traceparent", "")
    parts = parent.split("-")
    if len(parts) >= 3 and len(parts[1]) == 32 and len(parts[2]) == 16:
        return parts[1], parts[2]
    return None, None


class LogContextFilter(logging.Filter):
    """Copy the log context onto each record (``record.correlation_id``…, ``None``
    when unset) and the current span's ``trace_id`` and ``span_id``. Add it to
    a handler so formatters, including plain text ones, can use the fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_resource_engine", False):
            return True
        context = _context.get() or {}
        for name in CONTEXT_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, context.get(name))
        if not hasattr(record, "trace_id"):
            record.trace_id, record.span_id = _trace_ids()
        record._resource_engine = True
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ``timestamp`` (UTC, ISO 8601), ``level``,
    ``logger``, ``message``, the log context fields that are set, ``trace_id``
    and ``span_id``, ``exception`` (the formatted traceback) and every
    ``extra=`` field.

    Args:
        static: Fields added to every line (``{"service": "billing"}``).
    """

    def __init__(self, *, static: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self.static = dict(static or {})
        self._filter = LogContextFilter()

    def format(self, record: logging.LogRecord) -> str:
        self._filter.filter(record)  # when the handler has no LogContextFilter
        line: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **self.static,
        }
        for name in (*CONTEXT_FIELDS, "trace_id", "span_id"):
            value = getattr(record, name, None)
            if value is not None:
                line[name] = value
        for name, value in record.__dict__.items():
            if name not in _STANDARD and name not in line and not name.startswith("_"):
                if name in CONTEXT_FIELDS or name in ("trace_id", "span_id"):
                    continue
                line[name] = value
        if record.exc_info:
            line["exception"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            line.setdefault("error_type", error_type(record.exc_info[1]))
        elif record.exc_text:
            line["exception"] = record.exc_text
        if record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, default=str, ensure_ascii=False)


def error_type(error: BaseException | None) -> str | None:
    """The engine's code for ``error`` (``RE-E702``, a ``DomainError`` code), or
    its class name."""
    if error is None:
        return None
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code else type(error).__name__


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s"


def configure_logging(
    *,
    format: Literal["json", "text"] = "text",
    level: int | str = logging.INFO,
    stream: TextIO | None = None,
    static: Mapping[str, Any] | None = None,
) -> logging.Handler:
    """Replace the root logger's handlers with one stream handler (stderr by
    default) carrying the log context. ``text`` shows the correlation id in
    brackets; ``json`` writes one ``JsonFormatter`` object per line. Returns the
    handler."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(LogContextFilter())
    if format == "json":
        handler.setFormatter(JsonFormatter(static=static))
    elif format == "text":
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    else:
        raise ValueError(f"format must be 'json' or 'text', not {format!r}")
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper() if isinstance(level, str) else level)
    return handler


__all__ = [
    "CONTEXT_FIELDS",
    "JsonFormatter",
    "LogContextFilter",
    "bind_log_context",
    "configure_logging",
    "error_type",
    "log_context",
    "update_log_context",
]
