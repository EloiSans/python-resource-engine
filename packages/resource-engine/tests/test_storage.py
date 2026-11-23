from __future__ import annotations

import pytest
from resource_engine.testing import InMemoryBlobStore
from resource_engine.testing.contracts import BlobStoreContract


class TestInMemoryBlobStore(BlobStoreContract):
    @pytest.fixture
    def blob_store(self):
        return InMemoryBlobStore()

    @pytest.fixture
    def presigned_client(self, blob_store):
        return blob_store.upload, blob_store.download
