"""Fixtures for Valkey/Redis (see compose.emulators.yaml, port 6380)."""

from __future__ import annotations

import os
import socket

import pytest

URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6380), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("redis",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason="Valkey/Redis not reachable on 127.0.0.1:6380")
    for item in selected:
        item.add_marker(skip)


@pytest.fixture
async def client():
    from redis.asyncio import Redis

    redis = Redis.from_url(URL)
    yield redis
    await redis.aclose()
