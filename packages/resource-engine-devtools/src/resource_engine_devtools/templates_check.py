"""``check`` RE-C007: an ``EmailRequested`` names a template set the
application's renderer doesn't have.

The renderer is ``[tool.resource-engine] templates = "pkg.module:templates"``.
Hooks and actions of every resource are read (and the application functions
they call, like RE-C006); each ``EmailRequested(template="...")`` with a
literal name needs a subject and a text or html body, for its literal
``locale`` or, without one, unlocalised. Requests built with a computed name
can't be checked here.
"""

from __future__ import annotations

import ast
import inspect
import types
from collections.abc import Callable, Iterator
from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.email import EmailRequested

from resource_engine_devtools.check import Violation
from resource_engine_devtools.events_check import _code
from resource_engine_devtools.hooks_check import (
    _UNRESOLVED,
    MAX_DEPTH,
    _is_application,
    _parse,
    _resolve,
    _scope,
)


def check_templates(
    registry: ResourceRegistry, renderer: Any, *, prefix: str = "email/"
) -> list[Violation]:
    violations: list[Violation] = []
    seen: set[tuple[str, int]] = set()
    for definition in registry:
        if definition.resource_cls.__module__.startswith("resource_engine."):
            continue
        for fn, extra, where in _code(definition):
            for path, line, template, locale in _requests(fn, 0, set(), extra):
                if (path, line) in seen:
                    continue
                seen.add((path, line))
                missing = _missing(renderer, prefix, template, locale)
                if missing:
                    violations.append(
                        Violation(
                            code="RE-C007",
                            path=path,
                            line=line,
                            message=f"{where} requests the email {template!r}"
                            + (f" in {locale!r}" if locale else "")
                            + f", but the templates lack {missing}",
                            hint=f"Add {prefix}{template}.subject.txt and "
                            f"{prefix}{template}.txt or .html to the templates folder.",
                        )
                    )
    return violations


def _missing(renderer: Any, prefix: str, template: str, locale: str | None) -> str | None:
    base = f"{prefix}{template}"
    subject = f"{base}.subject.txt"
    if not renderer.exists(subject, locale=locale):
        return subject
    if not (
        renderer.exists(f"{base}.txt", locale=locale)
        or renderer.exists(f"{base}.html", locale=locale)
    ):
        return f"{base}.txt or {base}.html"
    return None


def _requests(
    fn: Callable[..., Any], depth: int, visited: set[int], extra: dict[str, Any] | None
) -> Iterator[tuple[str, int, str, str | None]]:
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
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            template = keywords.get("template")
            if isinstance(template, ast.Constant) and isinstance(template.value, str):
                locale = keywords.get("locale")
                yield (
                    path,
                    first_line + node.lineno - 1,
                    template.value,
                    locale.value if isinstance(locale, ast.Constant) else None,
                )
            continue
        if target is _UNRESOLVED:
            continue
        if isinstance(target, types.FunctionType | types.MethodType) and _is_application(target):
            inner = {"self": extra["self"]} if extra and "self" in extra else None
            yield from _requests(target, depth + 1, visited, inner)
