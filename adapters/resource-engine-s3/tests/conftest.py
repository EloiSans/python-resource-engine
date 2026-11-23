"""Fixtures for Garage (see compose.emulators.yaml and docker/garage/init.py)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ENDPOINT = os.getenv("GARAGE_S3_ENDPOINT", "http://127.0.0.1:3910")
BUCKET = "resource-engine"
KEY_ID = "GK5e6f1a2b3c4d5e6f708192a3"
KEY_SECRET = "5e6f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7"


def _garage_up() -> bool:
    host, port = ENDPOINT.split("//", 1)[1].rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            pass
    except OSError:
        return False
    init = subprocess.run(
        [sys.executable, str(ROOT / "docker/garage/init.py")], capture_output=True, timeout=60
    )
    return init.returncode == 0


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("garage",)
    ]
    if not selected or _garage_up():
        return
    skip = pytest.mark.skip(reason=f"Garage not reachable at {ENDPOINT}")
    for item in selected:
        item.add_marker(skip)


@pytest.fixture
async def s3():
    from aiobotocore.session import get_session
    from resource_engine_s3 import s3_client_options

    session = get_session()
    async with session.create_client(
        "s3",
        aws_access_key_id=KEY_ID,
        aws_secret_access_key=KEY_SECRET,
        **s3_client_options(ENDPOINT, region="garage", path_style=True),
    ) as client:
        yield client


@pytest.fixture
def prefix():
    return f"tests/{uuid.uuid4().hex[:10]}/"
