"""Fixtures for RabbitMQ (see compose.emulators.yaml)."""

from __future__ import annotations

import os
import socket
import uuid

import pytest

URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5672), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("rabbitmq",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason="RabbitMQ not reachable on 127.0.0.1:5672")
    for item in selected:
        item.add_marker(skip)


@pytest.fixture
async def channel():
    import aio_pika

    connection = await aio_pika.connect_robust(URL)
    async with connection:
        yield await connection.channel(publisher_confirms=True, on_return_raises=True)


@pytest.fixture
async def topology(channel):
    from resource_engine_rabbitmq import declare_topology

    name = f"re-test-{uuid.uuid4().hex[:10]}"
    topo = await declare_topology(channel, exchange=name, queue=name)
    yield topo
    await topo.queue.delete(if_unused=False, if_empty=False)
    if topo.dead_letter_queue is not None:
        await topo.dead_letter_queue.delete(if_unused=False, if_empty=False)
    await topo.exchange.delete()
