"""Event declaration lint (``resource-engine check``): RE-C006.

Hooks and action ``execute`` methods that record a domain event the resource
does not declare in ``Resource.events``. Undeclared events are still
published, but they are missing from the generated docs and the AsyncAPI
document, so consumers cannot know about them.

The lint reads ``record_event(EventClass(...))`` calls and resolves the class
through the function's globals and closure. The engine's own events (created,
updated, transitions, documents) need no declaration. Helpers defined in the
application are followed, a few calls deep.
"""

from __future__ import annotations

import ast
import inspect
import types
from collections.abc import Callable, Iterator
from typing import Any

from resource_engine import ResourceDefinition, ResourceRegistry
from resource_engine.application.events import ResourceEvent
from resource_engine.domain import DomainEvent

from resource_engine_devtools.check import Violation
from resource_engine_devtools.hooks_check import (
    _UNRESOLVED,
    MAX_DEPTH,
    _is_application,
    _name,
    _parse,
    _resolve,
    _scope,
)

_ENGINE_EVENT_MODULES = (
    "resource_engine.application.events",
    "resource_engine.documents.events",
)


def check_events(registry: ResourceRegistry) -> list[Violation]:
    violations: list[Violation] = []
    for definition in registry:
        if definition.resource_cls.__module__.startswith("resource_engine."):
            continue  # the engine's built-in resources
        declared = set(definition.events)
        seen: set[tuple[str, int]] = set()
        for fn, extra, where in _code(definition):
            for path, line, event, chain in _recorded(fn, depth=0, visited=set(), extra=extra):
                if event in declared or _is_engine_event(event) or (path, line) in seen:
                    continue
                seen.add((path, line))
                via = f" (via {' → '.join(chain)})" if chain else ""
                violations.append(
                    Violation(
                        code="RE-C006",
                        path=path,
                        line=line,
                        message=f"{_name(fn)} ({where}) records {event.__name__}, which "
                        f"{definition.name} does not declare{via}",
                        hint=f"Add {event.__name__} to {definition.resource_cls.__name__}.events "
                        "so the docs and the AsyncAPI document list it.",
                    )
                )
    return violations


def _code(
    definition: ResourceDefinition,
) -> Iterator[tuple[Callable[..., Any], dict[str, Any] | None, str]]:
    for key, hooks in definition.hooks.items():
        for hook in hooks:
            yield hook, None, f"{definition.name} {key}"
    for action in definition.actions.values():
        execute = getattr(action.action_cls, "execute", None)
        if execute is not None:
            yield (
                execute,
                {"self": action.action_cls},
                f"{definition.name} action {action.name}",
            )


def _is_engine_event(event: type) -> bool:
    return event.__module__ in _ENGINE_EVENT_MODULES or (
        issubclass(event, ResourceEvent) and event.__module__.startswith("resource_engine.")
    )


def _recorded(
    fn: Callable[..., Any], *, depth: int, visited: set[int], extra: dict[str, Any] | None
) -> Iterator[tuple[str, int, type, list[str]]]:
    fn = inspect.unwrap(fn)
    if id(fn) in visited or depth > MAX_DEPTH:
        return
    visited.add(id(fn))
    parsed = _parse(fn)
    if parsed is None:
        return
    tree, path, first_line = parsed
    scope = _scope(fn) | (extra or {})
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = first_line + node.lineno - 1
        if isinstance(node.func, ast.Attribute) and node.func.attr == "record_event":
            for arg in node.args[:1]:
                if isinstance(arg, ast.Call):
                    event = _resolve(arg.func, scope)
                    if isinstance(event, type) and issubclass(event, DomainEvent):
                        yield path, line, event, []
            continue
        target = _resolve(node.func, scope)
        if target is _UNRESOLVED:
            continue
        if isinstance(target, types.FunctionType | types.MethodType) and _is_application(target):
            inner = {"self": extra["self"]} if extra and "self" in extra else None
            for inner_path, inner_line, event, chain in _recorded(
                target, depth=depth + 1, visited=visited, extra=inner
            ):
                yield inner_path, inner_line, event, [_name(target), *chain]
