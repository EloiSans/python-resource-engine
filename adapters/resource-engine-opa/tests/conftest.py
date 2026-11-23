"""OPA fixtures (see compose.emulators.yaml; policies in docker/opa/)."""

from __future__ import annotations

import os
import urllib.request

import pytest

OPA_URL = os.getenv("OPA_URL", "http://127.0.0.1:8181")


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"{OPA_URL}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("opa",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason=f"OPA not reachable on {OPA_URL}")
    for item in selected:
        item.add_marker(skip)


@pytest.fixture
async def opa_engine():
    from resource_engine_opa import OpaEngine

    engine = OpaEngine(OPA_URL)
    yield engine
    await engine.client.aclose()
