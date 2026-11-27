"""Mailpit (compose.emulators.yaml): SMTP on 127.0.0.1:1026, API on :8026."""

from __future__ import annotations

import socket

import pytest


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 1026), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("mailpit",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason="Mailpit not reachable on 127.0.0.1:1026")
    for item in selected:
        item.add_marker(skip)
