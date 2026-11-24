"""The evaluation harness scores what it should: the reference solutions get
everything, doing nothing fails the hidden tests, and the classic mistake
(an e-mail sent inside the transaction) is caught by check and the hidden tests."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_tasks_load():
    tasks = {t.id: t for t in run.load_tasks()}
    assert set(tasks) == {"tickets-with-state-machine", "notify-and-reassign"}
    assert tasks["notify-and-reassign"].setup == ["tickets-with-state-machine"]
    for task in tasks.values():
        assert task.prompt and task.hidden_tests
        assert (task.path / "solution").is_dir()
        assert all((task.path / name).is_file() for name in task.hidden_tests)


def test_reference_solutions_score_full_marks():
    scores = run.evaluate("reference", run.load_tasks(), list(run.MODES))
    assert [(s.task, s.mode, s.total) for s in scores] == [
        ("notify-and-reassign", "with", 4),
        ("notify-and-reassign", "without", 4),
        ("tickets-with-state-machine", "with", 4),
        ("tickets-with-state-machine", "without", 4),
    ], [s.details for s in scores]


def test_doing_nothing_fails_the_hidden_tests():
    (score,) = run.evaluate("noop", run.load_tasks(["tickets-with-state-machine"]), ["without"])
    assert score.passed == {"check": True, "docs": True, "tests": True, "hidden": False}


def test_email_inside_the_transaction_is_caught(tmp_path):
    task = run.load_tasks(["notify-and-reassign"])[0]
    wrong = tmp_path / "wrong"
    run.overlay(task.path / "solution", wrong)
    resource = wrong / "src/helpdesk/tickets/application/resource.py"
    text = resource.read_text()
    resource.write_text(
        text.replace(
            '"after_commit": [hooks.email_customer]', '"after_transition": [hooks.notify_now]'
        )
    )
    hooks = wrong / "src/helpdesk/tickets/application/hooks.py"
    hooks.write_text(
        hooks.read_text()
        + '''

async def notify_now(ctx: OperationContext) -> None:
    """Tells the customer right away."""
    if ctx.transition == "resolve":
        await send_mail(ctx)


async def send_mail(ctx: OperationContext) -> None:
    """Sends the e-mail."""
    await email_customer(ctx)
'''
    )
    agent = f"cp -r {shlex.quote(str(wrong))}/. ."
    (score,) = run.evaluate(agent, [task], ["without"])
    assert not score.passed["check"] and "RE-C002" in score.details["check"]
    assert not score.passed["hidden"]


def test_markdown_report():
    score = run.Score("t", "with", "x", dict.fromkeys(run.CHECKS, True), {}, 1.0)
    text = run.markdown([score])
    assert "| t | with | ✓ | ✓ | ✓ | ✓ | 4/4 |" in text and "with the skill: 4/4" in text
