"""Locate the application's ResourceRegistry."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

from resource_engine import ResourceRegistry


def find_config(start: Path) -> dict[str, object]:
    for directory in (start, *start.parents):
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            config = data.get("tool", {}).get("resource-engine")
            if config:
                return {**config, "_root": directory}
    return {}


def add_paths(config: dict[str, object], cwd: Path) -> None:
    """Put ``[tool.resource-engine] paths`` and the working directory on ``sys.path``."""
    root = Path(config.get("_root", cwd))  # type: ignore[arg-type]
    for extra in config.get("paths", []):  # type: ignore[union-attr]
        path = str((root / str(extra)).resolve())
        if path not in sys.path:
            sys.path.insert(0, path)
    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))


def load_registry(spec: str | None, cwd: Path) -> ResourceRegistry:
    """Load ``module:attribute``, falling back to ``[tool.resource-engine] registry``."""
    config = find_config(cwd)
    add_paths(config, cwd)
    spec = spec or config.get("registry")  # type: ignore[assignment]
    if not spec:
        raise SystemExit(
            "No registry given. Pass --app module:attribute or set "
            '[tool.resource-engine] registry = "pkg.bootstrap:registry" in pyproject.toml'
        )
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    registry = getattr(module, attr or "registry")
    if not isinstance(registry, ResourceRegistry):
        raise SystemExit(f"{spec} is not a ResourceRegistry")
    return registry


def load_authz(cwd: Path) -> object | None:
    """``[tool.resource-engine] authz = "pkg.bootstrap:authz"`` (an ``AuthzConfig``)."""
    spec = find_config(cwd).get("authz")
    if not spec:
        return None
    module_name, _, attr = str(spec).partition(":")
    return getattr(importlib.import_module(module_name), attr or "authz")


def load_object(spec: str) -> object:
    """``module:attribute``."""
    module_name, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit(f"{spec!r} must be module:attribute")
    return getattr(importlib.import_module(module_name), attr)


def load_consumers(specs: list[str] | None, cwd: Path) -> list[object]:
    """``EventRouter`` objects from ``specs``, falling back to
    ``[tool.resource-engine] consumers``. An entry may also name a function
    returning a router; it is called without arguments."""
    from resource_engine.messaging import EventRouter

    config = find_config(cwd)
    add_paths(config, cwd)
    routers = []
    for spec in specs or config.get("consumers", []):  # type: ignore[union-attr]
        target = load_object(str(spec))
        if not isinstance(target, EventRouter) and callable(target):
            target = target()
        if not isinstance(target, EventRouter):
            raise SystemExit(f"{spec} is not an EventRouter")
        routers.append(target)
    return routers
