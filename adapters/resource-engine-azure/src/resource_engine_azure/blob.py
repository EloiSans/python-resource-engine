"""Azure Blob Storage as the documents' ``BlobStore`` (extra ``[blob]``).

```python
from azure.storage.blob.aio import ContainerClient
from resource_engine_azure import AzureBlobStore

container = ContainerClient.from_connection_string(CONNECTION_STRING, "documents")
store = AzureBlobStore(container, account_key=ACCOUNT_KEY)   # the key: presigned (SAS) URLs
```

- **Uploads** go in blocks, committed only once the whole content is in:
  past ``max_bytes`` or on an error nothing is committed, and uncommitted
  blocks expire on their own, so no partial blob is left.
- **Presigned URLs** are SAS URLs signed with the account key (or a user
  delegation key you sign yourself): PUT with ``x-ms-blob-type: BlockBlob``,
  GET with the download name as ``Content-Disposition``.
- The content's sha256 is stored as blob metadata.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from resource_engine.documents.content import Measured
from resource_engine.documents.model import content_disposition
from resource_engine.errors import BlobNotFound, StorageFailed
from resource_engine.ports.storage import BlobInfo, Content, PresignedRequest


class AzureBlobStore:
    """``BlobStore`` and ``SupportsPresignedUrls`` over an async ``ContainerClient``.

    Args:
        container: ``azure.storage.blob.aio.ContainerClient`` (owned by the caller).
        prefix: Prepended to every key.
        block_size: Size of the uploaded blocks.
        account_key: The storage account key, to sign SAS URLs; without it the
            store has no presigned URLs.
    """

    def __init__(
        self,
        container: Any,
        *,
        prefix: str = "",
        block_size: int = 4 * 1024 * 1024,
        account_key: str | None = None,
    ) -> None:
        self.container = container
        self.prefix = prefix
        self.block_size = block_size
        self.account_key = account_key

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _failed(self, operation: str, exc: Exception) -> StorageFailed:
        return StorageFailed(
            f"Azure Blob {operation} on {self.container.container_name!r} failed: {exc}",
            hint="Check the connection string and that the container exists.",
            details={"operation": operation, "container": self.container.container_name},
        )

    async def put(
        self, key: str, content: Content, *, content_type: str, max_bytes: int | None = None
    ) -> BlobInfo:
        measured = Measured(content, max_bytes=max_bytes)
        blob = self.container.get_blob_client(self._key(key))
        blocks: list[str] = []
        buffer = bytearray()

        async def stage(data: bytes) -> None:
            block_id = base64.b64encode(uuid.uuid4().hex.encode()).decode()
            await blob.stage_block(block_id, data)
            blocks.append(block_id)

        try:
            async for chunk in measured:
                buffer += chunk
                while len(buffer) >= self.block_size:
                    await stage(bytes(buffer[: self.block_size]))
                    buffer = buffer[self.block_size :]
            if buffer or not blocks:
                await stage(bytes(buffer))
            response = await blob.commit_block_list(
                blocks,
                content_settings=ContentSettings(content_type=content_type),
                metadata={"sha256": measured.sha256},
            )
        except AzureError as exc:
            raise self._failed("put", exc) from exc
        return BlobInfo(
            key=key,
            size=measured.size,
            content_type=content_type,
            etag=(response.get("etag") or "").strip('"') or None,
            last_modified=response.get("last_modified") or datetime.now(UTC),
            sha256=measured.sha256,
        )

    async def open(self, key: str) -> AsyncIterator[bytes]:
        blob = self.container.get_blob_client(self._key(key))
        try:
            stream = await blob.download_blob()
        except ResourceNotFoundError as exc:
            raise BlobNotFound(f"No blob {key!r}") from exc
        except AzureError as exc:
            raise self._failed("get", exc) from exc
        async for chunk in stream.chunks():
            yield chunk

    async def head(self, key: str) -> BlobInfo | None:
        blob = self.container.get_blob_client(self._key(key))
        try:
            props = await blob.get_blob_properties()
        except ResourceNotFoundError:
            return None
        except AzureError as exc:
            raise self._failed("head", exc) from exc
        return BlobInfo(
            key=key,
            size=props.size,
            content_type=props.content_settings.content_type,
            etag=(props.etag or "").strip('"') or None,
            last_modified=props.last_modified,
            sha256=(props.metadata or {}).get("sha256"),
        )

    async def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            try:
                await self.container.delete_blob(self._key(key))
            except ResourceNotFoundError:
                continue
            except AzureError as exc:
                raise self._failed("delete", exc) from exc

    async def list(self, prefix: str = "") -> AsyncIterator[BlobInfo]:
        try:
            async for item in self.container.list_blobs(name_starts_with=self._key(prefix)):
                yield BlobInfo(
                    key=item.name[len(self.prefix) :],
                    size=item.size,
                    content_type=item.content_settings.content_type,
                    etag=(item.etag or "").strip('"') or None,
                    last_modified=item.last_modified,
                )
        except AzureError as exc:
            raise self._failed("list", exc) from exc

    # ------------------------------------------------------------ presigned

    def _sas_url(
        self, key: str, permission: BlobSasPermissions, expires: timedelta, **kw: Any
    ) -> str:
        if not self.account_key:
            raise StorageFailed(
                "Presigned URLs need the account key",
                hint="AzureBlobStore(container, account_key=...).",
            )
        blob = self.container.get_blob_client(self._key(key))
        token = generate_blob_sas(
            account_name=blob.account_name,
            container_name=blob.container_name,
            blob_name=blob.blob_name,
            account_key=self.account_key,
            permission=permission,
            expiry=datetime.now(UTC) + expires,
            **kw,
        )
        return f"{blob.url}?{token}"

    async def presign_put(
        self, key: str, *, content_type: str, expires: timedelta
    ) -> PresignedRequest:
        url = self._sas_url(key, BlobSasPermissions(create=True, write=True), expires)
        headers = {"x-ms-blob-type": "BlockBlob", "Content-Type": content_type}
        return PresignedRequest(url, "PUT", datetime.now(UTC) + expires, headers)

    async def presign_get(
        self, key: str, *, expires: timedelta, download_name: str | None = None
    ) -> PresignedRequest:
        extra = {"content_disposition": content_disposition(download_name)} if download_name else {}
        url = self._sas_url(key, BlobSasPermissions(read=True), expires, **extra)
        return PresignedRequest(url, "GET", datetime.now(UTC) + expires)


__all__ = ["AzureBlobStore"]
