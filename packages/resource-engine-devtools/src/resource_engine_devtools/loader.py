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


def load_registry(spec: str | None, cwd: Path) -> ResourceRegistry:
    """Load ``module:attribute``, falling back to ``[tool.resource-engine] registry``."""
    config = find_config(cwd)
    root = Path(config.get("_root", cwd))  # type: ignore[arg-type]
    for extra in config.get("paths", []):  # type: ignore[union-attr]
        path = str((root / str(extra)).resolve())
        if path not in sys.path:
            sys.path.insert(0, path)
    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))
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
