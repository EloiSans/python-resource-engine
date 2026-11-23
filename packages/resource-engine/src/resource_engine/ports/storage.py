"""Blob storage port: binary content of documents (S3, Garage, MinIO, Azure Blob, GCS...)."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

type Content = bytes | AsyncIterable[bytes]


@dataclass(frozen=True)
class BlobInfo:
    key: str
    size: int
    content_type: str | None = None
    etag: str | None = None
    last_modified: datetime | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class PresignedRequest:
    """An HTTP request a client can make directly against the store."""

    url: str
    method: str
    expires_at: datetime
    headers: dict[str, str] = field(default_factory=dict)


class BlobStore(Protocol):
    """Stores opaque binary content under string keys.

    Keys are relative; adapters may add their own prefix. ``put`` must not leave a
    partial object behind when it fails or when ``max_bytes`` is exceeded.
    """

    async def put(
        self, key: str, content: Content, *, content_type: str, max_bytes: int | None = None
    ) -> BlobInfo:
        """Store ``content`` (streamed), returning size and sha256.

        Raises ``DocumentTooLarge`` (RE-E603) past ``max_bytes`` and
        ``StorageFailed`` (RE-E607) on store errors.
        """
        ...

    def open(self, key: str) -> AsyncIterator[bytes]:
        """Stream the content. Raises ``BlobNotFound`` (RE-E608)."""
        ...

    async def head(self, key: str) -> BlobInfo | None: ...

    async def delete(self, keys: Sequence[str]) -> None:
        """Delete keys; missing keys are ignored."""
        ...

    def list(self, prefix: str = "") -> AsyncIterator[BlobInfo]: ...


@runtime_checkable
class SupportsPresignedUrls(Protocol):
    """A store that can hand out time-limited URLs for direct upload and download."""

    async def presign_put(
        self, key: str, *, content_type: str, expires: timedelta
    ) -> PresignedRequest: ...

    async def presign_get(
        self, key: str, *, expires: timedelta, download_name: str | None = None
    ) -> PresignedRequest: ...
