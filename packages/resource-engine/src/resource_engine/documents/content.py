"""Streaming helpers shared by blob store adapters."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable, AsyncIterator

from resource_engine.errors import DocumentTooLarge
from resource_engine.ports.storage import Content


async def iter_chunks(content: Content, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    if isinstance(content, bytes | bytearray | memoryview):
        data = bytes(content)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]
        return
    async for chunk in content:
        if chunk:
            yield bytes(chunk)


class Measured:
    """Wraps content, counting bytes and hashing them while it is streamed.

    Iterating raises ``DocumentTooLarge`` as soon as ``max_bytes`` is exceeded, so
    adapters can abort the upload without reading the rest.
    """

    def __init__(self, content: Content, *, max_bytes: int | None = None) -> None:
        self._content = content
        self.max_bytes = max_bytes
        self.size = 0
        self._hash = hashlib.sha256()

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in iter_chunks(self._content):
            self.size += len(chunk)
            if self.max_bytes is not None and self.size > self.max_bytes:
                raise DocumentTooLarge(
                    f"Content exceeds the limit of {self.max_bytes} bytes",
                    details={"max_bytes": self.max_bytes},
                )
            self._hash.update(chunk)
            yield chunk


async def read_all(stream: AsyncIterable[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])
