"""Gate lints (``resource-engine check``): RE-C004 and RE-C005.

- **RE-C004**: an enabled operation of the application (CRUD, transition,
  action, document) that only a catch-all system role can perform. No other
  system role and no policy of the seed grants it, so in practice only
  ``admin`` can use it. The seed is the list of policies the application
  creates (``[tool.resource-engine] policies = "pkg.authz:SEED_POLICIES"``);
  without it the rule is skipped. Conditions are ignored: a conditional grant
  is still a gate.
- **RE-C005**: the registry could not be loaded because the engine refused a
  declaration (a state field in a create schema, a bad hook point...). It is
  reported with the engine's code and hint, at the line of the application code
  that triggered it.
"""

from __future__ import annotations

import importlib
import inspect
import traceback
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from resource_engine import ResourceRegistry
from resource_engine.authz.catalog import FIELD_KINDS, Catalog, action_matches
from resource_engine.errors import ResourceEngineError

from resource_engine_devtools.check import Violation


def load_policies(spec: str | Iterable[str]) -> list[Mapping[str, Any]]:
    """``module:attribute`` (or several) naming lists of policies: dicts as
    sent to ``authz_policies``, or ``Policy`` objects."""
    specs = [spec] if isinstance(spec, str) else list(spec)
    policies: list[Mapping[str, Any]] = []
    for item in specs:
        module_name, _, attr = str(item).partition(":")
        value = getattr(importlib.import_module(module_name), attr or "SEED_POLICIES")
        for policy in value:
            policies.append(policy if isinstance(policy, Mapping) else vars(policy))
    return policies


def check_gates(
    registry: ResourceRegistry, authz: Any, policies: list[Mapping[str, Any]]
) -> list[Violation]:
    catalog = Catalog.from_registry(registry)
    roles = list(getattr(authz, "system_roles", ()) or ())
    catch_all = {r.name for r in roles if "*" in r.grants}
    gates = [r for r in roles if r.name not in catch_all]
    allows = [p for p in policies if p.get("effect") == "allow" and p.get("enabled", True)]
    violations: list[Violation] = []
    for info in catalog.actions.values():
        if info.kind in FIELD_KINDS or info.resource not in registry:
            continue
        definition = registry.get(info.resource)
        if definition.resource_cls.__module__.startswith("resource_engine."):
            continue  # built-in resources (authz_*, audit_log, documents)
        operation = info.name.split(":", 1)[1]
        if info.kind == "crud" and operation in definition.internal_operations:
            continue  # run by code (processes, jobs), not by callers
        if info.kind == "transition" and not definition.expose_transitions:
            continue
        granted = any(
            action_matches(g, info.name, wildcards=info.wildcards or role.include_sensitive)
            for role in gates
            for g in role.grants
        ) or any(
            action_matches(pattern, info.name, wildcards=info.wildcards)
            for policy in allows
            for pattern in policy.get("actions", ())
        )
        if granted:
            continue
        path, line = _location(definition.resource_cls)
        only = ", ".join(sorted(catch_all)) or "nobody"
        violations.append(
            Violation(
                code="RE-C004",
                path=path,
                line=line,
                message=f"{info.name} is enabled but only {only} can perform it: no other "
                "system role and no seed policy grants it",
                hint="Add a policy to the seed, grant it to a system role, or remove the "
                "operation (operations, transitions, actions or document slots).",
            )
        )
    return violations


def load_error(exc: ResourceEngineError) -> Violation:
    path, line = _user_frame(exc)
    return Violation(
        code="RE-C005",
        path=path,
        line=line,
        message=f"the registry could not be loaded: {exc.code} {exc.message}",
        hint=exc.hint or f"See {exc.doc}",
    )


def _location(cls: type) -> tuple[str, int]:
    try:
        return inspect.getsourcefile(cls) or "?", inspect.getsourcelines(cls)[1]
    except (OSError, TypeError):
        return "?", 0


def _user_frame(exc: BaseException) -> tuple[str, int]:
    """The innermost frame of the application (not the engine, not installed
    packages, not the import machinery)."""
    found = ("?", 0)
    for frame in traceback.extract_tb(exc.__traceback__):
        name = Path(frame.filename).as_posix()
        if (
            "/resource_engine/" in name
            or "/resource_engine_devtools/" in name
            or "site-packages" in name
            or name.startswith("<")
            or "importlib" in name
        ):
            continue
        found = (frame.filename, frame.lineno or 0)
    return found
