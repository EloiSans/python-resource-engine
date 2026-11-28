"""ClamAvScanner against a fake clamd (protocol) and the real one (compose, :3310)."""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest
from resource_engine.documents.model import DocumentRead
from resource_engine.documents.processors import chunks
from resource_engine.errors import RemoteCallFailed
from resource_engine_clamav import ClamAvScanner

#: The EICAR test file: harmless, detected by every antivirus as a test virus.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

DOC = DocumentRead(
    id="d1",
    owner_resource="invoices",
    owner_id="1",
    slot="files",
    status="processing",
    filename="f.txt",
    content_type="text/plain",
)


async def fake_clamd(reply_for):
    """A clamd speaking INSTREAM; ``reply_for(data)`` gives the verdict."""
    received = []

    async def handle(reader, writer):
        command = await reader.readuntil(b"\0")
        if command == b"zPING\0":
            writer.write(b"PONG\0")
        elif command == b"zINSTREAM\0":
            data = b""
            while True:
                (size,) = struct.unpack(">I", await reader.readexactly(4))
                if size == 0:
                    break
                data += await reader.readexactly(size)
            received.append(data)
            writer.write(reply_for(data).encode() + b"\0")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


async def test_clean_infected_and_errors():
    def verdict(data):
        if b"EICAR" in data:
            return "stream: Eicar-Signature FOUND"
        if data == b"huge":
            return "INSTREAM size limit exceeded. ERROR"
        return "stream: OK"

    server, port, received = await fake_clamd(verdict)
    async with server:
        scanner = ClamAvScanner("127.0.0.1", port)
        clean = await scanner.process(DOC, lambda: chunks(b"x" * 200_000))
        assert clean.reject is None and clean.metadata == {"engine": "clamav", "clean": True}
        assert received[0] == b"x" * 200_000
        infected = await scanner.process(DOC, lambda: chunks(EICAR))
        assert infected.reject == "virus found: Eicar-Signature"
        with pytest.raises(RemoteCallFailed, match="size limit"):
            await scanner.process(DOC, lambda: chunks(b"huge"))
        assert await scanner.ping()


async def test_clamd_down_is_retried_later():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    scanner = ClamAvScanner("127.0.0.1", port, timeout=2)
    with pytest.raises(RemoteCallFailed):
        await scanner.process(DOC, lambda: chunks(b"x"))
    assert not await scanner.ping()


def _clamd_ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3310), timeout=1) as conn:
            conn.sendall(b"zPING\0")
            return conn.recv(16).startswith(b"PONG")
    except OSError:
        return False


@pytest.mark.emulator("clamav")
@pytest.mark.skipif(not _clamd_ready(), reason="ClamAV not reachable on 127.0.0.1:3310")
async def test_real_clamd_finds_eicar():
    scanner = ClamAvScanner("127.0.0.1", 3310)
    assert (await scanner.version()).startswith("ClamAV")
    assert (await scanner.process(DOC, lambda: chunks(b"an invoice"))).reject is None
    infected = await scanner.process(DOC, lambda: chunks(EICAR, 10))
    assert infected.reject and "Eicar" in infected.reject
