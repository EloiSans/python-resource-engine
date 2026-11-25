"""The packaged AI skill: content checks, ai install / ai sync and the lock file."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from resource_engine import errors
from resource_engine_devtools.ai import example, installer
from resource_engine_devtools.cli import main

SKILL = example.SKILL_DIR
REPO = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ content


def test_skill_example_is_in_sync():
    source = REPO / "examples" / "invoices"
    stale = [
        name
        for name in example.EXAMPLE_FILES
        if (source / name).read_bytes() != (example.EXAMPLE_DIR / name).read_bytes()
    ]
    assert not stale, (
        f"skill example out of date: {stale}; run python -m resource_engine_devtools.ai.example"
    )


def test_skill_has_frontmatter_and_links_resolve():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"---\nname: resource-engine\ndescription: (.+?)\n---\n", text, re.S)
    assert front and len(front.group(1)) < 1024
    for md in [SKILL / "SKILL.md", SKILL / "llms.txt", *(SKILL / "references").glob("*.md")]:
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", md.read_text(encoding="utf-8")):
            if target.startswith("http") or target == "project.md":
                continue
            assert (md.parent / target).exists(), f"{md.name} links to missing {target}"


def test_every_error_code_is_explained():
    text = (SKILL / "references" / "errors.md").read_text(encoding="utf-8")
    codes = {
        cls.code
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.ResourceEngineError)
    }
    codes.discard(errors.ResourceEngineError.code)
    missing = sorted(c for c in codes if c not in text)
    assert not missing, f"references/errors.md lacks {missing}"


# ------------------------------------------------------------------ install


def test_install_writes_skill_blocks_and_lock(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Team\n\nOur own rules.\n")
    result = installer.install(tmp_path, ("claude", "agents", "cursor", "copilot"), "# ctx\n")
    skill = tmp_path / ".claude/skills/resource-engine"
    assert (skill / "SKILL.md").read_text() == (SKILL / "SKILL.md").read_text()
    assert (skill / "references/anti-patterns.md").is_file()
    assert (skill / "examples/invoices/application/resource.py").is_file()
    assert (skill / "project.md").read_text() == "# ctx\n"
    agents = (tmp_path / "AGENTS.md").read_text()
    assert agents.startswith("# Team\n\nOur own rules.\n")
    assert agents.count(installer.BEGIN) == 1 and ".claude/skills/resource-engine" in agents
    assert installer.BEGIN in (tmp_path / ".github/copilot-instructions.md").read_text()
    rule = (tmp_path / ".cursor/rules/resource-engine.mdc").read_text()
    assert rule.startswith("---\ndescription:") and "{block}" not in rule
    lock = json.loads((tmp_path / installer.LOCK).read_text())
    assert lock["targets"] == ["claude", "agents", "cursor", "copilot"]
    assert "AGENTS.md#block" in lock["files"]
    assert result["written"]
    again = installer.install(tmp_path, ("claude", "agents", "cursor", "copilot"), "# ctx\n")
    assert again["written"] == []
    assert (tmp_path / "AGENTS.md").read_text() == agents


def test_without_claude_the_skill_goes_to_resource_engine(tmp_path):
    installer.install(tmp_path, ("agents",), None)
    assert (tmp_path / ".resource-engine/skill/SKILL.md").is_file()
    assert "No registry was found" in (tmp_path / ".resource-engine/skill/project.md").read_text()
    assert "`.resource-engine/skill/SKILL.md`" in (tmp_path / "AGENTS.md").read_text()
    assert not (tmp_path / ".claude").exists()


def test_check_reports_each_state(tmp_path, monkeypatch):
    assert installer.check(tmp_path, None)[1][0].state == "missing"
    installer.install(tmp_path, ("claude", "agents"), "# v1\n")
    assert installer.check(tmp_path, "# v1\n")[1] == []
    states = {f.path: f.state for f in installer.check(tmp_path, "# v2\n")[1]}
    assert states == {".claude/skills/resource-engine/project.md": "out_of_date"}
    skill = tmp_path / ".claude/skills/resource-engine/SKILL.md"
    skill.write_text(skill.read_text() + "edit\n")
    (tmp_path / "AGENTS.md").write_text("gone\n")
    states = {f.path: f.state for f in installer.check(tmp_path, "# v1\n")[1]}
    assert states == {
        ".claude/skills/resource-engine/SKILL.md": "modified",
        "AGENTS.md#block": "missing",
    }
    monkeypatch.setattr(installer, "version", lambda: "9.9.9")
    assert any("the package is 9.9.9" in f.message for f in installer.check(tmp_path, "# v1\n")[1])


def test_sync_removes_unedited_files_of_older_skills(tmp_path):
    installer.install(tmp_path, ("claude",), None)
    lock = installer.read_lock(tmp_path)
    old = tmp_path / ".claude/skills/resource-engine/references/old.md"
    edited = tmp_path / ".claude/skills/resource-engine/references/edited.md"
    old.write_text("old\n")
    edited.write_text("mine\n")
    lock["files"][old.relative_to(tmp_path).as_posix()] = installer._hash("old\n")
    lock["files"][edited.relative_to(tmp_path).as_posix()] = installer._hash("theirs\n")
    installer.write_lock(tmp_path, lock)
    assert {f.state for f in installer.check(tmp_path, None)[1]} == {"stale"}
    result = installer.install(tmp_path, ("claude",), None)
    assert result["removed"] == [old.relative_to(tmp_path).as_posix()]
    assert not old.exists() and edited.exists()


def test_merge_block_replaces_in_place():
    first = installer.merge_block("intro\n", "one\n")
    second = installer.merge_block(first + "outro\n", "two\n")
    assert second == f"intro\n\n{installer.BEGIN}\ntwo\n{installer.END}\noutro\n"
    assert installer.extract_block(second) == "two\n"


def test_unknown_target_is_refused():
    with pytest.raises(SystemExit, match="Unknown targets: vim"):
        installer.parse_targets("claude,vim")


# ------------------------------------------------------------------ CLI


def test_cli_install_and_sync_check(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    app = ["--app", "invoices.bootstrap:registry"]
    assert main(["ai", "install", "--json", *app]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["targets"] == ["claude", "agents"]
    project = (tmp_path / ".claude/skills/resource-engine/project.md").read_text()
    assert "- invoices (" in project
    assert main(["ai", "sync", "--check", *app]) == 0
    capsys.readouterr()
    (tmp_path / ".claude/skills/resource-engine/project.md").write_text("stale\n")
    assert main(["ai", "sync", "--check", "--json", *app]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["findings"][0]["path"].endswith("project.md")
    assert main(["ai", "sync", *app]) == 0
    assert main(["ai", "sync", "--check", *app]) == 0


def test_cli_sync_without_install(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["ai", "sync"]) == 1
    assert "run resource-engine ai install" in capsys.readouterr().out


# --------------------------------------------------------------- MCP server


def test_install_registers_the_mcp_server(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "keep": 1})
    )
    targets = ("claude", "agents", "cursor", "copilot")
    command = installer.default_mcp_command(tmp_path)
    assert command == ["uv", "run", "resource-engine", "mcp"]
    result = installer.install(tmp_path, targets, None, mcp_command=command)
    assert ".mcp.json (resource-engine server)" in result["written"]

    claude = json.loads((tmp_path / ".mcp.json").read_text())
    assert claude["keep"] == 1 and claude["mcpServers"]["other"] == {"command": "x"}
    entry = {"command": "uv", "args": ["run", "resource-engine", "mcp"]}
    assert claude["mcpServers"]["resource-engine"] == entry
    cursor = json.loads((tmp_path / ".cursor/mcp.json").read_text())
    assert cursor == {"mcpServers": {"resource-engine": entry}}
    vscode = json.loads((tmp_path / ".vscode/mcp.json").read_text())
    assert vscode == {"servers": {"resource-engine": {"type": "stdio", **entry}}}
    assert installer.read_lock(tmp_path)["mcp_command"] == command

    lock, findings = installer.check(tmp_path, None)
    assert findings == []
    vscode["servers"]["resource-engine"]["args"] = ["mcp"]
    (tmp_path / ".vscode/mcp.json").write_text(json.dumps(vscode))
    (tmp_path / ".cursor/mcp.json").write_text("{}")
    states = {f.path: f.state for f in installer.check(tmp_path, None)[1]}
    assert states == {".vscode/mcp.json#mcp": "modified", ".cursor/mcp.json#mcp": "missing"}

    # sync restores them; installing without --mcp removes the unedited entries
    installer.install(tmp_path, targets, None, mcp_command=lock["mcp_command"])
    assert installer.check(tmp_path, None)[1] == []
    removed = installer.install(tmp_path, targets, None)["removed"]
    assert set(removed) >= {
        ".mcp.json (resource-engine server)",
        ".cursor/mcp.json (resource-engine server)",
        ".vscode/mcp.json (resource-engine server)",
    }
    claude = json.loads((tmp_path / ".mcp.json").read_text())
    assert claude == {"mcpServers": {"other": {"command": "x"}}, "keep": 1}
    assert not (tmp_path / ".cursor/mcp.json").exists()
    assert not (tmp_path / ".cursor").exists() or any((tmp_path / ".cursor").iterdir())


def test_mcp_configuration_with_comments_is_refused(tmp_path):
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode/mcp.json").write_text('{\n  // mine\n  "servers": {}\n}\n')
    with pytest.raises(SystemExit, match="not plain JSON"):
        installer.install(tmp_path, ("copilot",), None, mcp_command=["resource-engine", "mcp"])


def test_cli_install_mcp_and_sync(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    app = ["--app", "invoices.bootstrap:registry"]
    assert main(["ai", "install", "--mcp", "--json", *app]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mcp_command"] == ["resource-engine", "mcp"]  # no uv.lock here
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["resource-engine"]
    assert main(["ai", "sync", "--check", *app]) == 0
    (tmp_path / ".mcp.json").unlink()
    assert main(["ai", "sync", "--check", *app]) == 1
    capsys.readouterr()
    assert main(["ai", "sync", *app]) == 0  # keeps the MCP choice from the lock
    assert (tmp_path / ".mcp.json").is_file()
    assert main(["ai", "install", "--target", "agents", "--mcp", *app]) == 0
    assert "no target with an MCP configuration" in capsys.readouterr().out
    custom = ["--mcp-command", "poetry run resource-engine mcp"]
    assert main(["ai", "install", *custom, *app]) == 0
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["resource-engine"]
    assert entry == {"command": "poetry", "args": ["run", "resource-engine", "mcp"]}
