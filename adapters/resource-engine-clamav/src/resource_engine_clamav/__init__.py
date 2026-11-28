"""ClamAV antivirus scanning for the document processing pipeline.

```python
from resource_engine_clamav import ClamAvScanner

scanner = ClamAvScanner("clamav", 3310)          # or ClamAvScanner(socket="/run/clamav/clamd.sock")
DocumentPipeline(manager.documents, [scanner, ...]).register(router)
```

- **Streaming:** the content goes to ``clamd`` with ``INSTREAM`` in chunks,
  so large files are never held in memory. Keep ``StreamMaxLength`` in
  ``clamd.conf`` above the slots' ``max_bytes``.
- **Verdicts:** clean documents get ``metadata["antivirus"] = {"engine":
  "clamav", "clean": true}``; infected ones are rejected with the signature
  (``virus found: Win.Test.EICAR_HDB-1``) and never become available.
- **Failures** (clamd down, a timeout, a size limit) raise
  ``RemoteCallFailed``: the message is retried and dead-lettered, and the
  document stays ``processing``, never available unscanned.
- ``ping()`` and ``version()`` for health checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import AsyncIterator, Callable
from typing import Any

from resource_engine.errors import RemoteCallFailed

__all__ = ["ClamAvScanner"]

CHUNK = 64 * 1024


class ClamAvScanner:
    """``DocumentProcessor`` scanning documents with ``clamd``.

    Args:
        host / port: clamd's TCP address.
        socket: A Unix socket path instead of TCP.
        timeout: Seconds for one scan (connection, upload and verdict).
        accepts: Which documents to scan (default: all).
    """

    name = "antivirus"
    service = "clamav"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3310,
        *,
        socket: str | None = None,
        timeout: float = 60.0,
        accepts: Callable[[Any], bool] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.socket = socket
        self.timeout = timeout
        self._accepts = accepts

    def accepts(self, document: Any) -> bool:
        return self._accepts(document) if self._accepts is not None else True

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.socket:
            return await asyncio.open_unix_connection(self.socket)
        return await asyncio.open_connection(self.host, self.port)

    def _failed(self, operation: str, reason: str) -> RemoteCallFailed:
        where = self.socket or f"{self.host}:{self.port}"
        return RemoteCallFailed(
            f"ClamAV {operation} at {where} failed: {reason}",
            service=self.service,
            operation=operation,
            hint="Check that clamd is running and its StreamMaxLength fits the documents.",
        )

    async def _command(self, command: bytes, body: AsyncIterator[bytes] | None = None) -> str:
        writer = None
        try:
            reader, writer = await self._connect()
            writer.write(b"z" + command + b"\0")
            if body is not None:
                async for chunk in body:
                    for start in range(0, len(chunk), CHUNK):
                        part = chunk[start : start + CHUNK]
                        writer.write(struct.pack(">I", len(part)) + part)
                        await writer.drain()
                writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = await reader.readuntil(b"\0")
            return reply.rstrip(b"\0").decode("utf-8", "replace").strip()
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            raise self._failed(command.decode().lower(), f"{type(exc).__name__}: {exc}") from exc
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()

    async def scan(self, content: AsyncIterator[bytes]) -> str | None:
        """The signature found in ``content`` (``None``: clean)."""
        try:
            reply = await asyncio.wait_for(self._command(b"INSTREAM", content), self.timeout)
        except TimeoutError as exc:
            raise self._failed("instream", f"no verdict in {self.timeout}s") from exc
        verdict = reply.split(":", 1)[-1].strip()  # "stream: OK" / "stream: X FOUND"
        if verdict == "OK":
            return None
        if verdict.endswith(" FOUND"):
            return verdict.removesuffix(" FOUND").strip()
        raise self._failed("instream", reply)

    async def process(self, document: Any, content: Callable[[], AsyncIterator[bytes]]) -> Any:
        from resource_engine.documents.pipeline import ProcessingResult

        signature = await self.scan(content())
        if signature is not None:
            return ProcessingResult(
                metadata={"engine": "clamav", "clean": False, "signature": signature},
                reject=f"virus found: {signature}",
            )
        return ProcessingResult(metadata={"engine": "clamav", "clean": True})

    async def ping(self) -> bool:
        try:
            return await asyncio.wait_for(self._command(b"PING"), self.timeout) == "PONG"
        except (RemoteCallFailed, TimeoutError):
            return False

    async def version(self) -> str:
        """``ClamAV 1.4.1/27000/...``: the engine and signature database versions."""
        return await asyncio.wait_for(self._command(b"VERSION"), self.timeout)
