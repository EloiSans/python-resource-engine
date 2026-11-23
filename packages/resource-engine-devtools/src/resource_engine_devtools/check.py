"""Architecture lints (``resource-engine check``). Rule codes: RE-C0xx."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path

#: Top-level packages that a ``domain/`` package must never import.
FORBIDDEN_IN_DOMAIN = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pydantic",
    "resource_engine_fastapi",
    "resource_engine_sqlalchemy",
    "httpx",
    "requests",
)


@dataclass(frozen=True)
class Violation:
    code: str
    path: str
    line: int
    message: str
    hint: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, node.lineno))
    return found


def check_domain_imports(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        if "domain" not in path.relative_to(root).parts[:-1] or ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, line in _imports(tree):
            top = module.split(".")[0]
            if top in FORBIDDEN_IN_DOMAIN:
                violations.append(
                    Violation(
                        code="RE-C001",
                        path=str(path),
                        line=line,
                        message=f"domain module imports infrastructure package {module!r}",
                        hint="Move this code to application/ or infrastructure/, or depend on a "
                        "port defined in the domain/application layer.",
                    )
                )
    return violations
