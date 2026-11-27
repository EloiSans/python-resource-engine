"""GcsBlobStore against fake-gcs-server (compose, :4443)."""

from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from resource_engine.testing.contracts import BlobStoreContract

URL = "http://127.0.0.1:4443"


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 4443), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.emulator("fake-gcs")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason="fake-gcs-server not reachable on 127.0.0.1:4443"))


class TestGcsBlobStore(BlobStoreContract):
    @pytest.fixture
    async def blob_store(self):
        from resource_engine_gcp import GcsBlobStore

        bucket = f"b{uuid.uuid4().hex[:20]}"
        http = httpx.AsyncClient(timeout=30)
        (await http.post(f"{URL}/storage/v1/b", json={"name": bucket})).raise_for_status()
        yield GcsBlobStore(http, bucket, prefix="docs/", url=URL, chunk_size=256 * 1024)
        await http.aclose()
