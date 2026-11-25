"""``resource-engine worker``: run the application's ``JobRunner``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import inspect
import signal
import sys
from pathlib import Path
from typing import Any

from resource_engine.jobs import JobRunner
from resource_engine.logs import configure_logging

from resource_engine_devtools.loader import add_paths, find_config


def load_runner(spec: str | None, cwd: Path) -> Any:
    """``module:attribute``: a ``JobRunner``, or a callable (sync or async)
    returning one. Defaults to ``[tool.resource-engine] worker``."""
    config = find_config(cwd)
    add_paths(config, cwd)
    spec = spec or config.get("worker")  # type: ignore[assignment]
    if not spec:
        raise SystemExit(
            "No worker given. Pass --runner module:attribute or set "
            '[tool.resource-engine] worker = "pkg.worker:runner" in pyproject.toml'
        )
    module_name, _, attr = str(spec).partition(":")
    return getattr(importlib.import_module(module_name), attr or "runner")


async def _resolve(target: Any) -> JobRunner:
    runner = target
    if not isinstance(runner, JobRunner) and callable(runner):
        runner = runner()
        if inspect.isawaitable(runner):
            runner = await runner
    if not isinstance(runner, JobRunner):
        raise SystemExit(f"The worker target is not a JobRunner: {type(runner).__name__}")
    return runner


async def _run(args: argparse.Namespace) -> int:
    runner = await _resolve(load_runner(args.runner, Path.cwd()))
    if args.once:
        runs = await runner.run_once()
        for run in runs:
            print(f"{run.job}: {run.outcome} ({run.seconds:.2f} s)")
        return 1 if any(r.outcome == "failed" for r in runs) else 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows
            loop.add_signal_handler(sig, stop.set)
    await runner.run_forever(stop)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    configure_logging(
        format=getattr(args, "log_format", "text"), level=args.log_level, stream=sys.stderr
    )
    return asyncio.run(_run(args))
