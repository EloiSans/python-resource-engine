"""GotenbergPdfConverter against Gotenberg (compose, :3010)."""

from __future__ import annotations

import socket

import httpx
import pytest
from resource_engine.generation import DocumentGenerator
from resource_engine.templates import StringTemplates
from resource_engine_http import GotenbergPdfConverter

URL = "http://127.0.0.1:3010"


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3010), timeout=1):
            return True
    except OSError:
        return False


def gotenberg(test):
    test = pytest.mark.emulator("gotenberg")(test)
    if not _up():
        test = pytest.mark.skip(reason="Gotenberg not reachable on 127.0.0.1:3010")(test)
    return test


@gotenberg
async def test_html_becomes_a_pdf():
    async with httpx.AsyncClient(timeout=60) as http:
        generator = DocumentGenerator(
            StringTemplates({"documents/invoice.html": "<h1>Factura $number</h1>"}),
            pdf=GotenbergPdfConverter(http, URL),
        )
        document = await generator.generate("invoice", {"number": 7})
    assert document.content.startswith(b"%PDF-") and document.content_type == "application/pdf"
    assert len(document.content) > 1000


async def test_failures_are_remote_call_failures():
    from resource_engine.errors import RemoteCallFailed

    def broken(request):
        return httpx.Response(500, text="chromium crashed")

    converter = GotenbergPdfConverter(httpx.AsyncClient(transport=httpx.MockTransport(broken)), URL)
    with pytest.raises(RemoteCallFailed):
        await converter.html_to_pdf("<p>x</p>")
