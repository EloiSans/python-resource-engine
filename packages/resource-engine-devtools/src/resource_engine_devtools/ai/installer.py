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

With ``mcp`` (``ai install --mcp``) the development MCP server
(``resource-engine mcp``) is also registered for the targets' agents:
``.mcp.json`` (claude), ``.cursor/mcp.json`` (cursor) and ``.vscode/mcp.json``
(copilot). Only the ``resource-engine`` entry of those files is managed; other
servers and keys are kept.
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
SERVER_NAME = "resource-engine"
#: MCP configuration per target: file, key holding the servers, VS Code style entry.
MCP_CONFIGS: dict[str, tuple[Path, str, bool]] = {
    "claude": (Path(".mcp.json"), "mcpServers", False),
    "cursor": (Path(".cursor/mcp.json"), "mcpServers", False),
    "copilot": (Path(".vscode/mcp.json"), "servers", True),
}
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
    #: MCP configuration file → (key holding the servers, the managed entry).
    servers: dict[Path, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    mcp_command: list[str] | None = None


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


def default_mcp_command(root: Path) -> list[str]:
    """``uv run resource-engine mcp`` in uv projects, else ``resource-engine mcp``."""
    if (root / "uv.lock").is_file():
        return ["uv", "run", "resource-engine", "mcp"]
    return ["resource-engine", "mcp"]


def mcp_entry(command: Sequence[str], *, vscode: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": command[0], "args": list(command[1:])}
    return {"type": "stdio", **entry} if vscode else entry


def plan(
    targets: Sequence[str], context: str | None, mcp_command: Sequence[str] | None = None
) -> Plan:
    skill_path = CLAUDE_SKILL if "claude" in targets else PLAIN_SKILL
    result = Plan(tuple(targets), skill_path)
    if mcp_command:
        result.mcp_command = list(mcp_command)
        for target in targets:
            if target in MCP_CONFIGS:
                path, key, vscode = MCP_CONFIGS[target]
                result.servers[path] = (key, mcp_entry(mcp_command, vscode=vscode))
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


def install(
    root: Path,
    targets: Sequence[str],
    context: str | None,
    *,
    mcp_command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write the plan under ``root``; returns a summary (written, removed, kept).
    ``mcp_command``: register the development MCP server with this command."""
    lock = read_lock(root)
    the_plan = plan(targets, context, mcp_command)
    servers = {p: merge_server(_read(root / p), key, entry, p) for p, (key, entry) in
               the_plan.servers.items()}  # fmt: skip
    written: list[str] = []
    for path, content in the_plan.files.items():
        if _write(root / path, content):
            written.append(path.as_posix())
    for path, block in the_plan.blocks.items():
        if _write(root / path, merge_block(_read(root / path), block)):
            written.append(path.as_posix())
    for path, content in servers.items():
        if _write(root / path, content):
            written.append(f"{path.as_posix()} ({SERVER_NAME} server)")
    removed = _remove_stale(root, lock, the_plan)
    hashes = {p.as_posix(): _hash(c) for p, c in the_plan.files.items()}
    hashes |= {f"{p.as_posix()}#block": _hash(b) for p, b in the_plan.blocks.items()}
    hashes |= {f"{p.as_posix()}#mcp": _entry_hash(e) for p, (_, e) in the_plan.servers.items()}
    data: dict[str, Any] = {
        "version": version(),
        "targets": list(the_plan.targets),
        "skill_path": the_plan.skill_path.as_posix(),
        "files": hashes,
    }
    if the_plan.mcp_command:
        data["mcp_command"] = the_plan.mcp_command
    write_lock(root, data)
    return {
        "version": version(),
        "targets": list(the_plan.targets),
        "skill_path": the_plan.skill_path.as_posix(),
        "written": written,
        "removed": removed,
        "mcp_command": the_plan.mcp_command,
        "lock": LOCK.as_posix(),
    }


def _remove_stale(root: Path, lock: dict[str, Any] | None, the_plan: Plan) -> list[str]:
    """Files an earlier install wrote that the new plan no longer has, when the
    user has not edited them since."""
    if not lock:
        return []
    keep = {p.as_posix() for p in the_plan.files}
    keep |= {f"{p.as_posix()}#mcp" for p in the_plan.servers}
    removed = []
    for name, digest in lock.get("files", {}).items():
        if name.endswith("#block") or name in keep:
            continue
        if name.endswith("#mcp"):
            if _remove_server(root, name.removesuffix("#mcp"), digest):
                removed.append(f"{name.removesuffix('#mcp')} ({SERVER_NAME} server)")
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


def _load_config(current: str | None, path: Path) -> dict[str, Any]:
    if not current or not current.strip():
        return {}
    try:
        data = json.loads(current)
    except ValueError:
        raise SystemExit(
            f"{path} is not plain JSON (comments?); add the {SERVER_NAME} server by hand "
            "or remove the comments, then run the command again"
        ) from None
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not hold a JSON object")
    return data


def merge_server(current: str | None, key: str, entry: dict[str, Any], path: Path) -> str:
    """``current`` (an MCP configuration) with the managed server entry set."""
    data = _load_config(current, path)
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
    data[key] = {**servers, SERVER_NAME: entry}
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def extract_server(current: str | None, key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(current) if current else None
    except ValueError:
        return None
    servers = data.get(key) if isinstance(data, dict) else None
    entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else None


def _remove_server(root: Path, name: str, digest: str) -> bool:
    """Drop the managed entry of ``name`` (a configuration file relative to
    ``root``) when it is unedited; the file too when nothing else is left."""
    key = next((k for p, k, _ in MCP_CONFIGS.values() if p.as_posix() == name), None)
    path = root / name
    current = _read(path)
    entry = extract_server(current, key) if key else None
    if key is None or entry is None or _entry_hash(entry) != digest:
        return False
    data = json.loads(current or "{}")
    del data[key][SERVER_NAME]
    if not data[key]:
        del data[key]
    if data:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    else:
        path.unlink()
        _prune(path.parent, root)
    return True


def _entry_hash(entry: dict[str, Any]) -> str:
    return _hash(json.dumps(entry, sort_keys=True))


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
    the_plan = plan(lock.get("targets", DEFAULT_TARGETS), context, lock.get("mcp_command"))
    locked = lock.get("files", {})
    expected: dict[str, Any] = {p.as_posix(): c for p, c in the_plan.files.items()}
    expected |= {f"{p.as_posix()}#block": b for p, b in the_plan.blocks.items()}
    expected |= {f"{p.as_posix()}#mcp": e for p, (_, e) in the_plan.servers.items()}
    keys = {f"{p.as_posix()}#mcp": key for p, (key, _) in the_plan.servers.items()}
    for name, content in expected.items():
        if name.endswith("#mcp"):
            current_entry = extract_server(_read(root / name.removesuffix("#mcp")), keys[name])
            if current_entry is None:
                findings.append(Finding(name, "missing", f"no {SERVER_NAME} MCP server entry"))
            elif current_entry != content:
                edited = name in locked and _entry_hash(current_entry) != locked[name]
                findings.append(
                    Finding(
                        name,
                        "modified" if edited else "out_of_date",
                        "MCP server entry edited locally; ai sync will overwrite it"
                        if edited
                        else "MCP server entry differs from the current command",
                    )
                )
            continue
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
        if (root / name.removesuffix("#block").removesuffix("#mcp")).exists():
            findings.append(Finding(name, "stale", "no longer part of the skill"))
    return lock, findings


def summary(findings: Iterable[Finding]) -> str:
    lines = [f"{f.state:<12} {f.path}: {f.message}" for f in findings]
    return "\n".join(lines)
