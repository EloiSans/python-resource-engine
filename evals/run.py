"""Skill evaluation: run reference tasks with an AI coding agent, with and
without the Resource Engine skill installed, and score the result.

```bash
uv run python evals/run.py --agent reference                     # the harness itself
uv run python evals/run.py --agent 'claude -p "$(cat {prompt_file})" --permission-mode acceptEdits'
```

For every task and mode (``with``: ``resource-engine ai install`` first;
``without``: the bare project) the runner copies ``evals/fixture`` to a fresh
directory, applies the solutions of the tasks listed in ``setup``, writes the
prompt next to the project and runs the agent in the project directory. Then
it scores four checks:

- ``check``: ``resource-engine check`` reports no violations;
- ``docs``: ``resource-engine docs validate --strict`` passes;
- ``tests``: the project's own tests pass (the agent's included);
- ``hidden``: the task's hidden tests, copied in after the agent finished, pass.

``--agent reference`` copies each task's reference solution instead of calling
an agent; ``--agent noop`` changes nothing. Both are used by the harness tests.
A real agent runs as a shell command: ``{prompt_file}`` and ``{prompt}``
(shell-quoted) are replaced, and the workspace's ``bin`` directory is first on
``PATH`` so the agent finds ``resource-engine`` and ``pytest``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ElementTree
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

EVALS = Path(__file__).resolve().parent
FIXTURE = EVALS / "fixture"
TASKS = EVALS / "tasks"
MODES = ("with", "without")
CHECKS = ("check", "docs", "tests", "hidden")


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    path: Path
    setup: list[str] = field(default_factory=list)
    hidden_tests: list[str] = field(default_factory=list)


@dataclass
class Score:
    task: str
    mode: str
    agent: str
    passed: dict[str, bool]
    details: dict[str, str]
    seconds: float
    project: str | None = None

    @property
    def total(self) -> int:
        return sum(self.passed.values())

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"total": self.total, "of": len(CHECKS)}


def load_tasks(selected: list[str] | None = None) -> list[Task]:
    tasks = []
    for path in sorted(TASKS.glob("*/task.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        tasks.append(
            Task(
                id=data["id"],
                title=data.get("title", data["id"]),
                prompt=data["prompt"].strip() + "\n",
                path=path.parent,
                setup=list(data.get("setup", [])),
                hidden_tests=list(data.get("score", {}).get("hidden_tests", [])),
            )
        )
    if selected:
        unknown = set(selected) - {t.id for t in tasks}
        if unknown:
            raise SystemExit(f"Unknown tasks: {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t.id in selected]
    return tasks


def overlay(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


def prepare(task: Task, tasks: dict[str, Task], workdir: Path, mode: str) -> Path:
    project = workdir / "project"
    shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns("__pycache__", ".*_cache"))
    for name in task.setup:
        overlay(tasks[name].path / "solution", project)
    if mode == "with":
        _run([*_cli(), "ai", "install"], project)
    return project


def run_agent(agent: str, task: Task, project: Path, timeout: float) -> str:
    if agent == "reference":
        overlay(task.path / "solution", project)
        return "reference solution applied"
    if agent == "noop":
        return "no changes"
    prompt_file = project.parent / "prompt.md"
    prompt_file.write_text(task.prompt, encoding="utf-8")
    command = agent.replace("{prompt_file}", shlex.quote(str(prompt_file))).replace(
        "{prompt}", shlex.quote(task.prompt)
    )
    result = subprocess.run(
        command,
        shell=True,
        cwd=project,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (project.parent / "agent.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    return f"exit {result.returncode}"


def score(task: Task, project: Path) -> tuple[dict[str, bool], dict[str, str]]:
    passed: dict[str, bool] = {}
    details: dict[str, str] = {}

    check = _run([*_cli(), "check", "--json", "src"], project)
    report = _json(check.stdout)
    passed["check"] = check.returncode == 0
    details["check"] = (
        ", ".join(sorted({v["code"] for v in report.get("violations", [])})) or "no violations"
        if report
        else _tail(check)
    )

    docs = _run([*_cli(), "docs", "validate", "--strict", "--json"], project)
    summary = _json(docs.stdout)
    passed["docs"] = docs.returncode == 0
    levels = [f.get("level") for f in summary.get("findings", [])]
    details["docs"] = (
        f"{levels.count('error')} errors, {levels.count('warning')} warnings"
        if summary
        else _tail(docs)
    )

    passed["tests"], details["tests"] = _pytest(project, [], project.parent / "tests.xml")

    hidden_dir = project / ".eval_hidden"
    hidden_dir.mkdir()
    for name in task.hidden_tests:
        shutil.copyfile(task.path / name, hidden_dir / Path(name).name)
    passed["hidden"], details["hidden"] = _pytest(
        project, [str(hidden_dir)], project.parent / "hidden.xml"
    )
    return passed, details


def evaluate(
    agent: str,
    tasks: list[Task],
    modes: list[str],
    *,
    timeout: float = 1800,
    keep: Path | None = None,
) -> list[Score]:
    every = {t.id: t for t in load_tasks()}
    scores = []
    for task in tasks:
        for mode in modes:
            workdir = Path(tempfile.mkdtemp(prefix=f"re-eval-{task.id}-{mode}-"))
            started = time.monotonic()
            project = prepare(task, every, workdir, mode)
            note = run_agent(agent, task, project, timeout)
            passed, details = score(task, project)
            details["agent"] = note
            kept = None
            if keep is not None:
                kept = keep / f"{task.id}-{mode}"
                shutil.rmtree(kept, ignore_errors=True)
                shutil.copytree(workdir, kept)
            shutil.rmtree(workdir, ignore_errors=True)
            scores.append(
                Score(
                    task.id,
                    mode,
                    agent,
                    passed,
                    details,
                    round(time.monotonic() - started, 1),
                    str(kept) if kept else None,
                )
            )
    return scores


def markdown(scores: list[Score]) -> str:
    lines = [
        "| Task | Skill | check | docs | tests | hidden | Score |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        marks = " | ".join("✓" if s.passed[c] else "✗" for c in CHECKS)
        lines.append(f"| {s.task} | {s.mode} | {marks} | {s.total}/{len(CHECKS)} |")
    by_mode: dict[str, list[int]] = {}
    for s in scores:
        by_mode.setdefault(s.mode, []).append(s.total)
    lines.append("")
    for mode, totals in by_mode.items():
        lines.append(f"- {mode} the skill: {sum(totals)}/{len(totals) * len(CHECKS)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- helpers


def _cli() -> list[str]:
    return [sys.executable, "-m", "resource_engine_devtools.cli"]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.pop("PYTHONPATH", None)
    return env


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=_env(), capture_output=True, text=True)


def _pytest(project: Path, paths: list[str], report: Path) -> tuple[bool, str]:
    result = _run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"]
        + paths,
        project,
    )
    if not report.exists():
        return False, _tail(result)
    suite = ElementTree.parse(report).getroot()
    suite = suite if suite.tag == "testsuite" else suite[0]
    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    ok = result.returncode == 0 and total > 0
    return ok, f"{total - failed}/{total} passed"


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    lines = (result.stdout + result.stderr).strip().splitlines()
    return lines[-1][:200] if lines else f"exit {result.returncode}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agent", required=True, help="reference, noop, or a shell command")
    parser.add_argument("--task", action="append", help="task id (repeatable; default: all)")
    parser.add_argument("--mode", action="append", choices=MODES, help="default: both")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds per agent run")
    parser.add_argument("--out", type=Path, help="directory for results.json and results.md")
    parser.add_argument("--keep", type=Path, help="keep every project here for inspection")
    args = parser.parse_args(argv)
    scores = evaluate(
        args.agent,
        load_tasks(args.task),
        args.mode or list(MODES),
        timeout=args.timeout,
        keep=args.keep,
    )
    table = markdown(scores)
    print(table, end="")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        payload = {"at": stamp, "agent": args.agent, "scores": [s.to_dict() for s in scores]}
        (args.out / f"results-{stamp}.json").write_text(json.dumps(payload, indent=2) + "\n")
        (args.out / f"results-{stamp}.md").write_text(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
