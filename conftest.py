"""Workspace-wide pytest settings.

``RESOURCE_ENGINE_REQUIRE_EMULATORS=1`` (the emulator CI job) turns "emulator
not reachable" skips into a failure, so a service that did not start cannot
pass as green.
"""

from __future__ import annotations

import os

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    if os.getenv("RESOURCE_ENGINE_REQUIRE_EMULATORS") != "1":
        return
    unreachable = sorted(
        {
            mark.kwargs.get("reason", "")
            for item in items
            if item.get_closest_marker("emulator")
            for mark in item.iter_markers("skip")
            if "not reachable" in mark.kwargs.get("reason", "")
        }
    )
    if unreachable:
        raise pytest.UsageError(
            "Emulators required but not reachable:\n  " + "\n  ".join(unreachable)
        )
