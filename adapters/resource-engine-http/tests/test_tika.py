"""TikaTextExtractor against a fake server and the real Tika (compose, :9998)."""

from __future__ import annotations

import socket

import httpx
import pytest
from resource_engine.documents.model import DocumentRead
from resource_engine.documents.processors import TextExtraction, chunks
from resource_engine.errors import RemoteCallFailed
from resource_engine_http import TikaTextExtractor


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF showing ``text`` (Helvetica), with a correct xref table."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


DOC = DocumentRead(
    id="d1",
    owner_resource="invoices",
    owner_id="1",
    slot="files",
    status="processing",
    filename="factura.pdf",
    content_type="application/pdf",
)


async def test_requests_and_unreadable_content():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.headers)))
        if request.headers["content-type"] == "application/x-broken":
            return httpx.Response(422)
        if request.url.path == "/tika":
            return httpx.Response(200, text="  Factura 7  \n")
        return httpx.Response(200, json={"xmpTPg:NPages": "2", "language": "ca"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tika = TikaTextExtractor(
        client, "http://tika:9998", types=("application/pdf", "application/x-broken")
    )
    found = await tika.extract(lambda: chunks(b"%PDF-"), "application/pdf")
    assert (found.text, found.pages, found.language) == ("Factura 7", 2, "ca")
    assert [p for p, _ in seen] == ["/tika", "/meta"]
    assert seen[0][1]["accept"] == "text/plain" and seen[0][1]["x-tika-ocrskipocr"] == "true"
    assert await tika.extract(lambda: chunks(b"?"), "application/x-broken") is None
    assert tika.handles("application/pdf") and not tika.handles("text/plain")


async def test_failures_are_retried():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(RemoteCallFailed):
        await TikaTextExtractor(client).extract(lambda: chunks(b"%PDF-"), "application/pdf")


def _tika_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 9998), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.emulator("tika")
@pytest.mark.skipif(not _tika_up(), reason="Tika not reachable on 127.0.0.1:9998")
async def test_real_tika_reads_a_pdf():
    async with httpx.AsyncClient(timeout=60) as http:
        processor = TextExtraction(TikaTextExtractor(http), slot="texts")
        result = await processor.process(DOC, lambda: chunks(minimal_pdf("Factura 2026-7")))
    assert result.metadata["extracted"] and result.metadata["pages"] == 1
    assert b"Factura 2026-7" in result.derived[0].content
