"""AzureBlobStore against Azurite (compose, :10000), with SAS URLs."""

from __future__ import annotations

import socket
import uuid

import httpx
import pytest
from resource_engine.testing.contracts import BlobStoreContract

ACCOUNT = "devstoreaccount1"
KEY = (  # Azurite's well-known development key (public, documented by Microsoft)
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)
CONNECTION = (
    f"DefaultEndpointsProtocol=http;AccountName={ACCOUNT};AccountKey={KEY};"
    f"BlobEndpoint=http://127.0.0.1:10000/{ACCOUNT};"
)


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 10000), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.emulator("azurite")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason="Azurite not reachable on 127.0.0.1:10000"))


class TestAzureBlobStore(BlobStoreContract):
    @pytest.fixture
    async def blob_store(self):
        from azure.storage.blob.aio import ContainerClient
        from resource_engine_azure import AzureBlobStore

        container = ContainerClient.from_connection_string(CONNECTION, f"c{uuid.uuid4().hex}")
        await container.create_container()
        yield AzureBlobStore(container, prefix="docs/", block_size=64 * 1024, account_key=KEY)
        await container.delete_container()
        await container.close()

    @pytest.fixture
    def presigned_client(self):
        async def upload(request, data):
            async with httpx.AsyncClient() as http:
                response = await http.put(request.url, content=data, headers=request.headers)
                response.raise_for_status()

        async def download(request):
            async with httpx.AsyncClient() as http:
                response = await http.get(request.url)
                response.raise_for_status()
                assert "nota.txt" in response.headers.get("content-disposition", "")
                return response.content

        return upload, download
