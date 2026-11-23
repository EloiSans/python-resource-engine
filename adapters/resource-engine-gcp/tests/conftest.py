"""Fixtures for the official Pub/Sub emulator (see compose.emulators.yaml)."""

from __future__ import annotations

import os
import socket
import uuid

import pytest

HOST = os.getenv("PUBSUB_EMULATOR_TEST_HOST", "127.0.0.1:8085")
PROJECT = "resource-engine-test"


def _emulator_up() -> bool:
    host, port = HOST.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if _emulator_up():
        return
    skip = pytest.mark.skip(reason=f"Pub/Sub emulator not reachable at {HOST}")
    for item in items:
        marker = item.get_closest_marker("emulator")
        if marker and marker.args[:1] == ("pubsub",):
            item.add_marker(skip)


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", HOST)
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient(
        publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
    )
    subscriber = pubsub_v1.SubscriberClient()
    yield publisher, subscriber
    subscriber.close()


@pytest.fixture
def topology(clients):
    publisher, subscriber = clients
    name = uuid.uuid4().hex[:12]
    topic = publisher.create_topic(name=f"projects/{PROJECT}/topics/re-{name}").name
    sub = subscriber.create_subscription(
        request={
            "name": f"projects/{PROJECT}/subscriptions/re-{name}",
            "topic": topic,
            "enable_message_ordering": True,
        }
    ).name
    return topic, sub
