"""Every package is publishable on its own: licence, metadata, version ranges,
and release tags that resolve to it."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import release_target  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = release_target.packages()


def project(path: Path) -> dict:
    return tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_package_is_found():
    assert len(PACKAGES) == 16 and "resource-engine" in PACKAGES


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_package_metadata(name):
    path = PACKAGES[name]
    data = project(path)
    meta = data["project"]
    assert meta["license"] == "Apache-2.0" and meta["license-files"] == ["LICENSE"]
    assert (path / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (path / "README.md").is_file() and meta["readme"] == "README.md"
    assert meta["description"] and meta["requires-python"] == ">=3.12"
    assert set(meta["urls"]) == {"Homepage", "Documentation", "Source", "Issues"}
    assert meta["urls"]["Source"].endswith(path.relative_to(ROOT).as_posix())
    assert any(c.startswith("License") for c in meta.get("classifiers", [])) is False  # SPDX only
    assert "hatchling>=1.27" in data["build-system"]["requires"]


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_dependencies_on_our_packages_have_ranges(name):
    meta = project(PACKAGES[name])["project"]
    deps = list(meta.get("dependencies", []))
    for extra in meta.get("optional-dependencies", {}).values():
        deps += extra
    for dep in deps:
        base = re.match(r"[A-Za-z0-9._-]+", dep).group(0).lower()
        if base in PACKAGES:
            assert re.search(r">=[\d.a-z]+,<\d", dep), f"{name}: {dep!r} needs a version range"


def test_release_tags():
    target = release_target.resolve("resource-engine-opa/v0.1.0.dev0")
    assert target == {
        "name": "resource-engine-opa",
        "path": "adapters/resource-engine-opa",
        "version": "0.1.0.dev0",
    }
    with pytest.raises(SystemExit, match="Bump the version"):
        release_target.resolve("resource-engine/v9.0.0")
    with pytest.raises(SystemExit, match="No package named"):
        release_target.resolve("resource-engine-nope/v0.1.0")
    with pytest.raises(SystemExit, match="is not <distribution>/v<version>"):
        release_target.resolve("v0.1.0")
