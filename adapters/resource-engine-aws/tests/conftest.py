"""Fixtures for the official AWS Emulator (see compose.emulators.yaml)."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import urllib.request
import uuid
from pathlib import Path

import pytest
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from resource_engine.messaging import EventEnvelope

ROOT = Path(__file__).resolve().parents[3]
ENDPOINT = os.getenv("AWS_EMULATOR_ENDPOINT", "https://127.0.0.1:9297")
CA = os.getenv("AWS_EMULATOR_CA", str(ROOT / ".emulators/aws/9297/ca.pem"))


def _emulator_problem() -> str | None:
    """Why the emulator can't be used (``None`` when it can)."""
    try:
        ctx = ssl.create_default_context(cafile=CA)
        with urllib.request.urlopen(f"{ENDPOINT}/__admin/health", context=ctx, timeout=2) as r:
            return None if r.status == 200 else f"health returned {r.status}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def pytest_collection_modifyitems(config, items):
    problem = _emulator_problem()
    if problem is None:
        return
    skip = pytest.mark.skip(reason=f"AWS Emulator not reachable at {ENDPOINT} ({problem})")
    for item in items:
        marker = item.get_closest_marker("emulator")
        if marker and marker.args[:1] == ("aws",):
            item.add_marker(skip)


@pytest.fixture
async def aws():
    session = get_session()
    kwargs = dict(
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="emulator",
        aws_secret_access_key="emulator",
        verify=CA,
        config=AioConfig(inject_host_prefix=False),
    )
    async with (
        session.create_client("sqs", **kwargs) as sqs,
        session.create_client("sns", **kwargs) as sns,
    ):
        yield sqs, sns


@pytest.fixture
def unique():
    return lambda prefix: f"{prefix}-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def receiver():
    return make_receiver


def make_receiver(sqs, queue_url: str):
    async def receive(expected: int, timeout: float = 15.0) -> list[EventEnvelope]:
        got: dict[str, EventEnvelope] = {}
        deadline = asyncio.get_running_loop().time() + timeout
        while len(got) < expected and asyncio.get_running_loop().time() < deadline:
            resp = await sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=1,
                MessageAttributeNames=["All"],
            )
            for msg in resp.get("Messages", []):
                body = json.loads(msg["Body"])
                if body.get("Type") == "Notification":  # SNS wrapper (non-raw delivery)
                    body = json.loads(body["Message"])
                env = EventEnvelope.from_dict(body)
                got[env.id] = env
                await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
        return list(got.values())

    return receive
