"""``resource-engine worker --once`` loads a runner (or a factory) and runs it."""

from __future__ import annotations

import json
import logging
import sys
import textwrap

import pytest
from resource_engine_devtools.cli import main

MODULE = textwrap.dedent(
    """
    from resource_engine.jobs import JobRunner

    calls = []


    async def tick():
        calls.append("tick")


    async def boom():
        raise RuntimeError("boom")


    runner = JobRunner()
    runner.add("tick", tick, every=60)


    async def build():
        r = JobRunner()
        r.add("boom", boom, every=60)
        return r


    not_a_runner = 42
    """
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "worker_app.py").write_text(MODULE)
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)  # the worker configures logging
    yield
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    sys.modules.pop("worker_app", None)


def test_once_runs_the_jobs(app, capsys):
    assert main(["worker", "--runner", "worker_app:runner", "--once"]) == 0
    assert "tick: ok" in capsys.readouterr().out
    assert sys.modules["worker_app"].calls == ["tick"]


def test_factory_and_failures(app, capsys):
    assert main(["worker", "--runner", "worker_app:build", "--once"]) == 1
    assert "boom: failed" in capsys.readouterr().out


def test_not_a_runner(app):
    with pytest.raises(SystemExit, match="not a JobRunner"):
        main(["worker", "--runner", "worker_app:not_a_runner", "--once"])


def test_pyproject_setting(app, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.resource-engine]\nworker = "worker_app:runner"\n'
    )
    assert main(["worker", "--once"]) == 0


def test_json_logs(app, capsys):
    assert main(["worker", "--runner", "worker_app:build", "--once", "--log-format", "json"]) == 1
    line = json.loads(capsys.readouterr().err.strip().splitlines()[0])
    assert line["message"].startswith("Job boom failed") and line["job"] == "boom"
    assert line["level"] == "ERROR" and line["error_type"] == "RuntimeError"
