"""``resource-engine ai install`` / ``ai sync``: put the skill and the project
context where AI coding agents look for them, and keep them current.

Targets:

- ``claude``: the skill in ``.claude/skills/resource-engine/``;
- ``agents``: a managed block in ``AGENTS.md``;
- ``cursor``: ``.cursor/rules/resource-engine.mdc``;
- ``copilot``: a managed block in ``.github/copilot-instructions.md``.

The skill files always go to one place (the Claude skill folder, or
``.resource-engine/skill/`` without the ``claude`` target), with the generated
project catalogue as ``project.md``. Managed blocks sit between
``<!-- resource-engine:begin -->`` and ``<!-- resource-engine:end -->``; the rest
of those files is never touched. ``.resource-engine/ai-lock.json`` records the
version, the targets and a hash of everything written, so ``ai sync --check``
can tell what is out of date and what was edited locally.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from resource_engine_devtools.ai.example import SKILL_DIR

TEMPLATES = Path(__file__).parent / "templates"
TARGETS = ("claude", "agents", "cursor", "copilot")
DEFAULT_TARGETS = ("claude", "agents")
LOCK = Path(".resource-engine/ai-lock.json")
BEGIN = "<!-- resource-engine:begin -->"
END = "<!-- resource-engine:end -->"
CLAUDE_SKILL = Path(".claude/skills/resource-engine")
PLAIN_SKILL = Path(".resource-engine/skill")


def version() -> str:
    """The installed devtools version (the skill ships with it)."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as dist_version

    try:
        return dist_version("resource-engine-devtools")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        from resource_engine import __version__

        return __version__


@dataclass
class Plan:
    """What an install writes: whole files and managed blocks, by path."""

    targets: tuple[str, ...]
    skill_path: Path
    files: dict[Path, str] = field(default_factory=dict)
    blocks: dict[Path, str] = field(default_factory=dict)


@dataclass
class Finding:
    path: str
    state: str  # missing | out_of_date | modified | stale | unlocked
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "state": self.state, "message": self.message}


def parse_targets(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_TARGETS
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    targets = tuple(dict.fromkeys(t.strip().lower() for t in items if t.strip()))
    unknown = [t for t in targets if t not in TARGETS]
    if unknown or not targets:
        raise SystemExit(
            f"Unknown targets: {', '.join(unknown) or '(none)'}. Use: {', '.join(TARGETS)}"
        )
    return targets


def plan(targets: Sequence[str], context: str | None) -> Plan:
    skill_path = CLAUDE_SKILL if "claude" in targets else PLAIN_SKILL
    result = Plan(tuple(targets), skill_path)
    for source in sorted(SKILL_DIR.rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            result.files[skill_path / source.relative_to(SKILL_DIR)] = source.read_text(
                encoding="utf-8"
            )
    result.files[skill_path / "project.md"] = context or _NO_CONTEXT
    block = _render(
        (TEMPLATES / "agents_block.md").read_text(encoding="utf-8"),
        version=version(),
        skill_path=skill_path.as_posix(),
    )
    if "agents" in targets:
        result.blocks[Path("AGENTS.md")] = block
    if "copilot" in targets:
        result.blocks[Path(".github/copilot-instructions.md")] = block
    if "cursor" in targets:
        rule = (TEMPLATES / "cursor_rule.mdc").read_text(encoding="utf-8")
        result.files[Path(".cursor/rules/resource-engine.mdc")] = rule.replace("{block}", block)
    return result


_NO_CONTEXT = """\
# Project catalogue

No registry was found when this file was generated. Set
`[tool.resource-engine] registry = "pkg.bootstrap:registry"` in pyproject.toml
and run `resource-engine ai sync`, or run `resource-engine inspect --json`.
"""


def _render(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


# ---------------------------------------------------------------- writing


def install(root: Path, targets: Sequence[str], context: str | None) -> dict[str, Any]:
    """Write the plan under ``root``; returns a summary (written, removed, kept)."""
    lock = read_lock(root)
    the_plan = plan(targets, context)
    written: list[str] = []
    for path, content in the_plan.files.items():
        if _write(root / path, content):
            written.append(path.as_posix())
    for path, block in the_plan.blocks.items():
        if _write(root / path, merge_block(_read(root / path), block)):
            written.append(path.as_posix())
    removed = _remove_stale(root, lock, the_plan)
    hashes = {p.as_posix(): _hash(c) for p, c in the_plan.files.items()}
    hashes |= {f"{p.as_posix()}#block": _hash(b) for p, b in the_plan.blocks.items()}
    write_lock(
        root,
        {
            "version": version(),
            "targets": list(the_plan.targets),
            "skill_path": the_plan.skill_path.as_posix(),
            "files": hashes,
        },
    )
    return {
        "version": version(),
        "targets": list(the_plan.targets),
        "skill_path": the_plan.skill_path.as_posix(),
        "written": written,
        "removed": removed,
        "lock": LOCK.as_posix(),
    }


def _remove_stale(root: Path, lock: dict[str, Any] | None, the_plan: Plan) -> list[str]:
    """Files an earlier install wrote that the new plan no longer has, when the
    user has not edited them since."""
    if not lock:
        return []
    keep = {p.as_posix() for p in the_plan.files}
    removed = []
    for name, digest in lock.get("files", {}).items():
        if name.endswith("#block") or name in keep:
            continue
        path = root / name
        if path.is_file() and _hash(path.read_text(encoding="utf-8")) == digest:
            path.unlink()
            removed.append(name)
            _prune(path.parent, root)
    return removed


def _prune(directory: Path, root: Path) -> None:
    while directory != root and directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
        directory = directory.parent


def merge_block(current: str | None, block: str) -> str:
    """Replace the managed block in ``current`` (or append it)."""
    managed = f"{BEGIN}\n{block.rstrip()}\n{END}\n"
    if not current:
        return managed
    start, end = current.find(BEGIN), current.find(END)
    if start != -1 and end > start:
        return current[:start] + managed + current[end + len(END) :].lstrip("\n")
    return current.rstrip("\n") + "\n\n" + managed


def extract_block(current: str | None) -> str | None:
    if not current:
        return None
    start, end = current.find(BEGIN), current.find(END)
    if start == -1 or end < start:
        return None
    return current[start + len(BEGIN) : end].strip("\n") + "\n"


def _write(path: Path, content: str) -> bool:
    if _read(path) == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def read_lock(root: Path) -> dict[str, Any] | None:
    path = root / LOCK
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def write_lock(root: Path, data: dict[str, Any]) -> None:
    _write(root / LOCK, json.dumps(data, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- checking


def check(root: Path, context: str | None) -> tuple[dict[str, Any] | None, list[Finding]]:
    """Compare what is installed with what an install would write now."""
    lock = read_lock(root)
    if lock is None:
        return None, [Finding(LOCK.as_posix(), "missing", "run resource-engine ai install")]
    findings: list[Finding] = []
    if lock.get("version") != version():
        findings.append(
            Finding(
                LOCK.as_posix(),
                "out_of_date",
                f"installed from resource-engine {lock.get('version')}, the package is {version()}",
            )
        )
    the_plan = plan(lock.get("targets", DEFAULT_TARGETS), context)
    locked = lock.get("files", {})
    expected = {p.as_posix(): c for p, c in the_plan.files.items()}
    expected |= {f"{p.as_posix()}#block": b for p, b in the_plan.blocks.items()}
    for name, content in expected.items():
        path = root / name.removesuffix("#block")
        current = _read(path)
        if name.endswith("#block"):
            current = extract_block(current)
        if current is None:
            findings.append(Finding(name, "missing", "not installed"))
        elif current != content:
            edited = name in locked and _hash(current) != locked[name]
            findings.append(
                Finding(
                    name,
                    "modified" if edited else "out_of_date",
                    "edited locally; ai sync will overwrite it"
                    if edited
                    else "differs from the current skill or project",
                )
            )
    for name in sorted(set(locked) - set(expected)):
        if (root / name.removesuffix("#block")).exists():
            findings.append(Finding(name, "stale", "no longer part of the skill"))
    return lock, findings


def summary(findings: Iterable[Finding]) -> str:
    lines = [f"{f.state:<12} {f.path}: {f.message}" for f in findings]
    return "\n".join(lines)
