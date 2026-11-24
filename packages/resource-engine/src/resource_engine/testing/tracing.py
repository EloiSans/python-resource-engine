"""``RecordingTracer``: an in-memory ``Tracer`` for tests (W3C-style ids)."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from resource_engine.ports.tracing import SpanKind
from resource_engine.tracing import NOOP_SPAN


@dataclass
class RecordedSpan:
    name: str
    kind: str
    trace_id: str
    span_id: str
    parent_id: str | None
    attributes: dict[str, Any] = field(default_factory=dict)
    errors: list[BaseException | str] = field(default_factory=list)
    ended: bool = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_error(self, error: BaseException | str) -> None:
        self.errors.append(error)


class RecordingTracer:
    """Records every span. ``spans`` are in start order."""

    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []
        self._current: ContextVar[RecordedSpan | None] = ContextVar("span", default=None)

    @asynccontextmanager
    async def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: Mapping[str, str] | None = None,
    ) -> AsyncIterator[RecordedSpan]:
        trace_id, parent_id = None, None
        remote = _parse(parent.get("traceparent")) if parent else None
        if remote is not None:
            trace_id, parent_id = remote
        elif (current := self._current.get()) is not None:
            trace_id, parent_id = current.trace_id, current.span_id
        span = RecordedSpan(
            name,
            kind,
            trace_id or secrets.token_hex(16),
            secrets.token_hex(8),
            parent_id,
            dict(attributes or {}),
        )
        self.spans.append(span)
        token = self._current.set(span)
        try:
            yield span
        except BaseException as exc:
            span.errors.append(exc)
            raise
        finally:
            span.ended = True
            self._current.reset(token)

    def current(self) -> Any:
        return self._current.get() or NOOP_SPAN

    def inject(self) -> dict[str, str]:
        span = self._current.get()
        return {"traceparent": f"00-{span.trace_id}-{span.span_id}-01"} if span else {}

    def named(self, name: str) -> RecordedSpan:
        matches = [s for s in self.spans if s.name == name]
        assert len(matches) == 1, (
            f"{len(matches)} spans named {name!r}: {[s.name for s in self.spans]}"
        )
        return matches[0]


def _parse(traceparent: str | None) -> tuple[str, str] | None:
    parts = (traceparent or "").split("-")
    return (parts[1], parts[2]) if len(parts) == 4 else None
