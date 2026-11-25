"""Configured tracer and the no-op default. See docs/observability.md.

```python
from resource_engine.tracing import set_tracer
from resource_engine_opentelemetry import OtelTracer

set_tracer(OtelTracer())
```
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from resource_engine.logs import bind_log_context, error_type
from resource_engine.ports.observability import OperationRun
from resource_engine.ports.tracing import Span, SpanKind, Tracer

logger = logging.getLogger("resource_engine")


class NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_error(self, error: BaseException | str) -> None:
        pass


NOOP_SPAN = NoopSpan()


class NoopTracer:
    """The default: no spans, empty carriers, nothing to configure."""

    @asynccontextmanager
    async def span(
        self,
        name: str,
        *,
        kind: SpanKind = "internal",
        attributes: Mapping[str, Any] | None = None,
        parent: Mapping[str, str] | None = None,
    ) -> AsyncIterator[Span]:
        yield NOOP_SPAN

    def current(self) -> Span:
        return NOOP_SPAN

    def inject(self) -> dict[str, str]:
        return {}


_tracer: Tracer = NoopTracer()


def set_tracer(tracer: Tracer | None) -> Tracer:
    """Use ``tracer`` everywhere (``None`` goes back to no tracing). Returns the
    previous one."""
    global _tracer
    previous = _tracer
    _tracer = tracer or NoopTracer()
    return previous


def get_tracer() -> Tracer:
    return _tracer


def _resource_name(manager: Any, resource: Any) -> str:
    if isinstance(resource, str):
        return resource
    try:
        return manager.registry.get(resource).name
    except Exception:
        return str(resource)


def traced(operation: str, *, resource: str | None = None) -> Callable[[Any], Any]:
    """Wrap a manager method in a span named ``"<resource> <operation>"``, a log
    context and a call to the manager's operation observers. The resource is
    the method's first argument unless given."""

    def decorate(fn: Any) -> Any:
        @functools.wraps(fn)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            manager = getattr(self, "manager", self)
            name = resource or (_resource_name(manager, args[0]) if args else "?")
            attributes = {"resource_engine.resource": name, "resource_engine.operation": operation}
            observers = getattr(manager, "observers", ())
            started = time.perf_counter()
            error: BaseException | None = None
            with bind_log_context(resource=name, operation=operation) as context:
                # a nested operation starts its own: drop the outer one's identity
                for key in ("correlation_id", "entity_id", "action"):
                    context.pop(key, None)
                try:
                    async with _tracer.span(f"{name} {operation}", attributes=attributes):
                        return await fn(self, *args, **kwargs)
                except BaseException as exc:
                    error = exc
                    raise
                finally:
                    if observers:
                        await _observe(
                            observers,
                            OperationRun(
                                resource=name,
                                operation=operation,
                                outcome="ok" if error is None else "failed",
                                seconds=time.perf_counter() - started,
                                action=context.get("action"),
                                error_type=error_type(error),
                                correlation_id=context.get("correlation_id"),
                                error=error,
                            ),
                        )

        return wrapper

    return decorate


async def _observe(observers: Any, run: OperationRun) -> None:
    for observer in observers:
        try:
            result = observer(run)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Operation observer %r failed", observer)


__all__ = ["NOOP_SPAN", "NoopSpan", "NoopTracer", "get_tracer", "set_tracer", "traced"]
