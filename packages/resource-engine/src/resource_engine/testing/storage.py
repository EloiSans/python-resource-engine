"""In-memory ``BlobStore`` with fake presigned URLs, for tests and prototypes."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from resource_engine.documents.content import Measured, read_all
from resource_engine.errors import BlobNotFound
from resource_engine.ports.storage import BlobInfo, Content, PresignedRequest


@dataclass
class _Blob:
    data: bytes
    info: BlobInfo


@dataclass
class InMemoryBlobStore:
    """Blobs in a dict. Presigned URLs look like ``memory://put/<token>``; tests
    complete them with ``upload(request, data)`` and ``download(request)``."""

    blobs: dict[str, _Blob] = field(default_factory=dict)
    _tokens: dict[str, tuple[str, str, datetime]] = field(default_factory=dict)

    async def put(
        self, key: str, content: Content, *, content_type: str, max_bytes: int | None = None
    ) -> BlobInfo:
        measured = Measured(content, max_bytes=max_bytes)
        data = await read_all(measured)
        info = BlobInfo(
            key=key,
            size=len(data),
            content_type=content_type,
            etag=measured.sha256[:32],
            last_modified=datetime.now(UTC),
            sha256=measured.sha256,
        )
        self.blobs[key] = _Blob(data, info)
        return info

    async def open(self, key: str) -> AsyncIterator[bytes]:
        blob = self.blobs.get(key)
        if blob is None:
            raise BlobNotFound(f"No blob {key!r}")
        yield blob.data

    async def head(self, key: str) -> BlobInfo | None:
        blob = self.blobs.get(key)
        return blob.info if blob else None

    async def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.blobs.pop(key, None)

    async def list(self, prefix: str = "") -> AsyncIterator[BlobInfo]:
        for key in sorted(self.blobs):
            if key.startswith(prefix):
                yield self.blobs[key].info

    async def presign_put(
        self, key: str, *, content_type: str, expires: timedelta
    ) -> PresignedRequest:
        token = secrets.token_hex(8)
        expires_at = datetime.now(UTC) + expires
        self._tokens[token] = ("PUT", key, expires_at)
        return PresignedRequest(
            f"memory://put/{token}", "PUT", expires_at, {"Content-Type": content_type}
        )

    async def presign_get(
        self, key: str, *, expires: timedelta, download_name: str | None = None
    ) -> PresignedRequest:
        token = secrets.token_hex(8)
        expires_at = datetime.now(UTC) + expires
        self._tokens[token] = ("GET", key, expires_at)
        return PresignedRequest(f"memory://get/{token}", "GET", expires_at)

    def _resolve(self, request: PresignedRequest, method: str) -> str:
        token = request.url.rsplit("/", 1)[-1]
        kind, key, expires_at = self._tokens[token]
        if kind != method or datetime.now(UTC) > expires_at:
            raise PermissionError("Presigned URL is invalid or expired")
        return key

    async def upload(self, request: PresignedRequest, data: bytes) -> None:
        """Simulate the client's direct upload to a presigned PUT URL."""
        key = self._resolve(request, "PUT")
        await self.put(key, data, content_type=request.headers.get("Content-Type", ""))

    async def download(self, request: PresignedRequest) -> bytes:
        return await read_all(self.open(self._resolve(request, "GET")))
