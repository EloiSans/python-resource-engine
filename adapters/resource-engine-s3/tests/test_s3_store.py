from __future__ import annotations

import httpx
import pytest
from resource_engine.errors import DocumentTooLarge
from resource_engine.testing.contracts import BlobStoreContract
from resource_engine_s3 import S3BlobStore

pytestmark = pytest.mark.emulator("garage")


@pytest.fixture
def store(s3, prefix):
    return S3BlobStore(s3, "resource-engine", prefix=prefix, part_size=5 * 1024 * 1024)


class TestS3OnGarage(BlobStoreContract):
    @pytest.fixture
    def blob_store(self, store):
        return store

    @pytest.fixture
    def presigned_client(self):
        async def upload(request, data):
            async with httpx.AsyncClient() as client:
                r = await client.request(
                    request.method, request.url, content=data, headers=request.headers
                )
                r.raise_for_status()

        async def download(request):
            async with httpx.AsyncClient() as client:
                r = await client.get(request.url)
                r.raise_for_status()
                assert "attachment" in r.headers.get("content-disposition", "")
                return r.content

        return upload, download


async def test_multipart_upload_above_part_size(store):
    async def chunks():
        for _ in range(12):
            yield b"z" * (1024 * 1024)

    info = await store.put("multi", chunks(), content_type="application/octet-stream")
    assert info.size == 12 * 1024 * 1024
    assert (await store.head("multi")).size == info.size
    total = 0
    async for chunk in store.open("multi"):
        total += len(chunk)
    assert total == info.size


async def test_multipart_aborted_past_max_bytes(store, s3):
    async def chunks():
        for _ in range(12):
            yield b"z" * (1024 * 1024)

    with pytest.raises(DocumentTooLarge):
        await store.put("too-big", chunks(), content_type="x/y", max_bytes=7 * 1024 * 1024)
    assert await store.head("too-big") is None
    uploads = await s3.list_multipart_uploads(Bucket="resource-engine", Prefix=store.prefix)
    assert not uploads.get("Uploads")
