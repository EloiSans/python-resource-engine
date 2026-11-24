"""Hook/effect lints (``resource-engine check``): RE-C002 and RE-C003.

- **RE-C002**: code that runs inside the transaction (hooks before
  ``after_commit``, action ``execute`` methods) reaches an external effect:
  a function marked ``@external_effect``, or a network, e-mail or cloud client
  (``httpx``, ``requests``, ``smtplib``, ``boto3``...). The effect happens even
  if the operation then rolls back. Helpers defined in the application are
  followed, a few calls deep.
- **RE-C003**: an ``after_commit`` hook does work that needs the transaction,
  which has already ended: recording events (the outbox has been written),
  using ``ctx.repository()`` / ``ctx.uow``, registering ``on_rollback``, or
  changing ``ctx.entity`` (the change is never saved).

The analysis reads source code and resolves names through the function's
globals and closure. Calls through objects it cannot resolve (``ctx.services``,
parameters) are not followed.
"""

from __future__ import annotations

import ast
import inspect
import sysconfig
import textwrap
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.application.hooks import HookPoint, has_external_effect

from resource_engine_devtools.check import Violation

#: Modules whose use inside a transaction is an external effect.
IO_MODULES = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "smtplib",
    "aiosmtplib",
    "boto3",
    "botocore",
    "aiobotocore",
    "aioboto3",
    "google.cloud",
    "azure",
    "aio_pika",
    "pika",
    "kafka",
    "aiokafka",
    "grpc",
    "paramiko",
    "ftplib",
    "socket",
)

MAX_DEPTH = 3

_STDLIB = (Path(sysconfig.get_paths()["stdlib"]).resolve(),)


def check_hooks(registry: ResourceRegistry) -> list[Violation]:
    violations: list[Violation] = []
    for definition in registry:
        for key, hooks in definition.hooks.items():
            for hook in hooks:
                where = f"{definition.name} {key}"
                if key.point is HookPoint.AFTER_COMMIT:
                    violations += _after_commit(hook, where)
                else:
                    violations += _in_transaction(hook, where)
        for action in definition.actions.values():
            execute = getattr(action.action_cls, "execute", None)
            if execute is not None:
                where = f"{definition.name} action {action.name}"
                violations += _in_transaction(execute, where, {"self": action.action_cls})
    return violations


# ------------------------------------------------------------------ RE-C002


def _in_transaction(
    fn: Callable[..., Any], where: str, extra: dict[str, Any] | None = None
) -> list[Violation]:
    found = next(_effects(fn, depth=0, seen=set(), extra=extra), None)
    if found is None:
        return []
    path, line, effect, chain = found
    via = f" (via {' → '.join(chain)})" if chain else ""
    return [
        Violation(
            code="RE-C002",
            path=path,
            line=line,
            message=f"{_name(fn)} runs inside the transaction ({where}) and {effect}{via}",
            hint="Move the effect to an after_commit hook or a consumer of a domain event, "
            "or stage it with ctx.on_rollback if it can be undone.",
        )
    ]


def _effects(
    fn: Callable[..., Any], *, depth: int, seen: set[int], extra: dict[str, Any] | None = None
) -> Iterator[tuple[str, int, str, list[str]]]:
    fn = inspect.unwrap(fn)
    if id(fn) in seen or depth > MAX_DEPTH:
        return
    seen.add(id(fn))
    parsed = _parse(fn)
    if parsed is None:
        return
    tree, path, first_line = parsed
    scope = _scope(fn) | (extra or {})
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve(node.func, scope)
        if target is _UNRESOLVED:
            continue
        line = first_line + node.lineno - 1
        if callable(target) and has_external_effect(target):
            yield path, line, f"calls {_name(target)}, marked @external_effect", []
            continue
        module = _module_of(target)
        if module and _is_io(module):
            yield path, line, f"uses {module} ({ast.unparse(node.func)})", []
            continue
        if isinstance(target, types.FunctionType | types.MethodType) and _is_application(target):
            inner_extra = {"self": extra["self"]} if extra and "self" in extra else None
            for inner_path, inner_line, effect, chain in _effects(
                target, depth=depth + 1, seen=seen, extra=inner_extra
            ):
                yield inner_path, inner_line, effect, [_name(target), *chain]
                return


# ------------------------------------------------------------------ RE-C003

_NEEDS_TRANSACTION = {
    "record_event": "records an event after the outbox was written",
    "repository": "uses ctx.repository() after the transaction ended",
    "on_rollback": "registers a compensation that can no longer run",
}


def _after_commit(fn: Callable[..., Any], where: str) -> list[Violation]:
    fn = inspect.unwrap(fn)
    parsed = _parse(fn)
    if parsed is None:
        return []
    tree, path, first_line = parsed
    func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)),
        None,
    )
    if func is None or not func.args.args:
        return []
    params = [a.arg for a in func.args.args]
    ctx = params[1] if params[0] in ("self", "cls") and len(params) > 1 else params[0]
    violations = []
    for node in ast.walk(func):
        problem = None
        if isinstance(node, ast.Call) and _is_attr_of(node.func, ctx):
            problem = _NEEDS_TRANSACTION.get(node.func.attr)  # type: ignore[union-attr]
        elif isinstance(node, ast.Attribute) and _is_attr_of(node, ctx) and node.attr == "uow":
            problem = "uses ctx.uow after the transaction ended"
        elif isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_changes_entity(t, ctx) for t in targets):
                problem = "changes ctx.entity, which is never saved after commit"
        if problem:
            violations.append(
                Violation(
                    code="RE-C003",
                    path=path,
                    line=first_line + node.lineno - 1,
                    message=f"{_name(fn)} ({where}) {problem}",
                    hint="Do this in a hook before commit (after_create, after_update, "
                    "after_transition...), or start a new operation through the manager.",
                )
            )
    return violations


def _is_attr_of(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == name
    )


def _changes_entity(target: ast.AST, ctx: str) -> bool:
    return (
        isinstance(target, ast.Attribute | ast.Subscript)
        and _is_attr_of(target.value, ctx)
        and (
            target.value.attr == "entity"  # type: ignore[attr-defined]
        )
    )


# ------------------------------------------------------------------ helpers

_UNRESOLVED = object()


def _parse(fn: Callable[..., Any]) -> tuple[ast.AST, str, int] | None:
    try:
        lines, first_line = inspect.getsourcelines(fn)
        path = inspect.getsourcefile(fn) or "?"
    except (OSError, TypeError):
        return None
    try:
        return ast.parse(textwrap.dedent("".join(lines))), path, first_line
    except SyntaxError:  # e.g. a lambda in the middle of an expression
        return None


def _scope(fn: Callable[..., Any]) -> dict[str, Any]:
    scope = dict(getattr(fn, "__globals__", {}))
    try:
        closure = inspect.getclosurevars(fn)
    except (TypeError, ValueError):
        return scope
    scope.update(closure.nonlocals)
    return scope


def _resolve(node: ast.AST, scope: dict[str, Any]) -> Any:
    """The object a dotted name refers to, rooted at globals/closure."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Call):  # httpx.AsyncClient().post → the constructor call
        return _UNRESOLVED
    if not isinstance(node, ast.Name) or node.id not in scope:
        return _UNRESOLVED
    value = scope[node.id]
    for part in reversed(parts):
        try:
            value = inspect.getattr_static(value, part)
        except AttributeError:
            try:
                value = getattr(value, part)
            except Exception:
                return _UNRESOLVED
        if isinstance(value, staticmethod | classmethod):
            value = value.__func__
    return value


def _module_of(value: Any) -> str | None:
    if isinstance(value, types.ModuleType):
        return value.__name__
    module = getattr(value, "__module__", None)
    if isinstance(module, str):
        return module
    return type(value).__module__


def _is_io(module: str) -> bool:
    return any(module == m or module.startswith(f"{m}.") for m in IO_MODULES)


def _is_application(fn: Callable[..., Any]) -> bool:
    """Defined in the application: not the engine, the standard library or an
    installed package."""
    if (getattr(fn, "__module__", "") or "").startswith("resource_engine"):
        return False
    try:
        path = Path(inspect.getsourcefile(fn) or "").resolve()
    except (OSError, TypeError):
        return False
    text = str(path)
    if "site-packages" in text or "dist-packages" in text:
        return False
    return not any(path.is_relative_to(root) for root in _STDLIB)


def _name(fn: Any) -> str:
    return getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or repr(fn)
