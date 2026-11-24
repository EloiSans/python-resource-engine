"""Resolve a release tag to the package it publishes.

Tags are ``<distribution>/v<version>``, e.g. ``resource-engine-opa/v0.2.0``.
The version must equal the one in the package's ``pyproject.toml``, so a tag
can't publish something other than what is in the repository.

    python scripts/release_target.py resource-engine-opa/v0.2.0
    name=resource-engine-opa
    path=adapters/resource-engine-opa
    version=0.2.0

With ``GITHUB_OUTPUT`` set, the lines are appended to it as step outputs.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(
    r"(?P<name>[a-z0-9][a-z0-9-]*)/v(?P<version>\d+\.\d+\.\d+(?:(?:a|b|rc|\.dev)\d+)?)"
)


def packages(root: Path = ROOT) -> dict[str, Path]:
    found = {}
    for pyproject in sorted(
        [*root.glob("packages/*/pyproject.toml"), *root.glob("adapters/*/pyproject.toml")]
    ):
        name = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
        found[name] = pyproject.parent
    return found


def resolve(tag: str, root: Path = ROOT) -> dict[str, str]:
    match = TAG.fullmatch(tag)
    if not match:
        raise SystemExit(
            f"Tag {tag!r} is not <distribution>/v<version> (e.g. resource-engine/v0.2.0)"
        )
    name, version = match["name"], match["version"]
    found = packages(root)
    if name not in found:
        raise SystemExit(f"No package named {name!r}; packages: {', '.join(found)}")
    path = found[name]
    declared = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    if declared != version:
        raise SystemExit(
            f"Tag {tag} says version {version}, but {path.relative_to(root)}/pyproject.toml "
            f"says {declared}. Bump the version in pyproject.toml first."
        )
    return {"name": name, "path": path.relative_to(root).as_posix(), "version": version}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: release_target.py <distribution>/v<version>")
    lines = [f"{key}={value}" for key, value in resolve(argv[0]).items()]
    print("\n".join(lines))
    if output := os.getenv("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
