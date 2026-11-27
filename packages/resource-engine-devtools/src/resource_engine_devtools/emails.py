"""Where the application requests emails: every ``EmailRequested(...)`` in the
hooks and actions of the registered resources (and the application functions
they call), read from the source. Used by ``check`` (RE-C007) and the
generated docs.
"""

from __future__ import annotations

import ast
import inspect
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from resource_engine import ResourceDefinition, ResourceRegistry
from resource_engine.email import EmailRequested

from resource_engine_devtools.hooks_check import (
    _UNRESOLVED,
    MAX_DEPTH,
    _is_application,
    _parse,
    _resolve,
    _scope,
)


@dataclass(frozen=True)
class EmailUse:
    """One ``EmailRequested`` call.

    Attributes:
        resource: The resource whose hook or action requests it.
        point: The hook point (``after_transition:approve``) or ``action:<name>``.
        function: The hook or action that requests it (or calls the helper that does).
        template: The template set, ``None`` when computed at runtime.
        locale: A literal locale, if any.
        to: The recipients as written (``[customer.email]``).
        path / line: Where the call is.
    """

    resource: str
    point: str
    function: str
    template: str | None
    locale: str | None
    to: str
    path: str
    line: int


def find_email_requests(registry: ResourceRegistry) -> list[EmailUse]:
    uses: list[EmailUse] = []
    for definition in registry:
        if definition.resource_cls.__module__.startswith("resource_engine."):
            continue
        for point, fn, extra in _code(definition):
            name = getattr(fn, "__qualname__", repr(fn))
            for path, line, call in _calls(fn, 0, set(), extra):
                keywords = {k.arg: k.value for k in call.keywords if k.arg}
                uses.append(
                    EmailUse(
                        resource=definition.name,
                        point=point,
                        function=name,
                        template=_literal(keywords.get("template")),
                        locale=_literal(keywords.get("locale")),
                        to=ast.unparse(keywords["to"]) if "to" in keywords else "",
                        path=path,
                        line=line,
                    )
                )
    return uses


def _code(definition: ResourceDefinition) -> Iterator[tuple[str, Callable[..., Any], Any]]:
    for key, hooks in definition.hooks.items():
        for hook in hooks:
            yield str(key), hook, None
    for action in definition.actions.values():
        execute = getattr(action.action_cls, "execute", None)
        if execute is not None:
            yield f"action:{action.name}", execute, {"self": action.action_cls}


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _calls(
    fn: Callable[..., Any], depth: int, visited: set[int], extra: dict[str, Any] | None
) -> Iterator[tuple[str, int, ast.Call]]:
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
        target = _resolve(node.func, scope)
        if target is EmailRequested:
            yield path, first_line + node.lineno - 1, node
            continue
        if target is _UNRESOLVED:
            continue
        if isinstance(target, types.FunctionType | types.MethodType) and _is_application(target):
            inner = {"self": extra["self"]} if extra and "self" in extra else None
            yield from _calls(target, depth + 1, visited, inner)
