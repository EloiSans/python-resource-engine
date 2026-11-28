"""Reference document processors: the content-type check and text extraction."""

from __future__ import annotations

import io
import zipfile

import pytest
from resource_engine.documents.model import DocumentRead
from resource_engine.documents.processors import (
    BuiltinTextExtractor,
    ContentTypeCheck,
    TextExtraction,
    chunks,
    detect,
)


def doc(content_type: str, filename: str = "file.bin", slot: str = "files") -> DocumentRead:
    return DocumentRead(
        id="d1",
        owner_resource="invoices",
        owner_id="1",
        slot=slot,
        status="processing",
        filename=filename,
        content_type=content_type,
    )


def opener(data: bytes):
    return lambda: chunks(data, 7)


def docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.parametrize(
    ("declared", "data", "reject"),
    [
        ("application/pdf", b"%PDF-1.7\n...", None),
        ("image/png", b"\x89PNG\r\n\x1a\n....", None),
        (DOCX, docx("a"), None),
        ("text/csv", b"a,b\n1,2\n", None),
        ("image/png", b"%PDF-1.7", "declared image/png, but the content is application/pdf"),
        ("image/png", b"MZ\x90\x00", "the content is an executable (application/x-msdownload)"),
        ("application/pdf", b"hello", "the content is not application/pdf"),
    ],
)
async def test_content_type_check(declared, data, reject):
    result = await ContentTypeCheck().process(doc(declared), opener(data))
    assert result.reject == reject
    if reject is None:
        assert result.metadata["declared"] == declared


def test_detect_webp_and_unknown():
    assert detect(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detect(b"plain") is None


async def test_builtin_extractor_reads_text_and_docx():
    extractor = BuiltinTextExtractor()
    assert extractor.handles("text/plain; charset=utf-8") and extractor.handles(DOCX)
    assert not extractor.handles("application/pdf")
    text = await extractor.extract(opener("Factura àèí".encode()), "text/plain")
    assert text.text == "Factura àèí"
    latin = await extractor.extract(opener("cafè".encode("latin-1")), "text/plain; charset=latin-1")
    assert latin.text == "cafè"
    word = await extractor.extract(opener(docx("Primer", "Segon")), DOCX)
    assert word.text == "Primer\nSegon"
    assert await extractor.extract(opener(b"not a zip"), DOCX) is None
    small = BuiltinTextExtractor(max_bytes=3)
    assert await small.extract(opener(b"too long"), "text/plain") is None


async def test_text_extraction_derives_a_text_document():
    processor = TextExtraction(slot="texts", preview=5)
    document = doc(DOCX, "contracte.docx")
    assert processor.accepts(document)
    assert not processor.accepts(doc("text/plain", "contracte.txt", slot="texts"))
    assert not processor.accepts(doc("application/pdf"))
    result = await processor.process(document, opener(docx("Contracte de servei")))
    assert result.metadata == {"extracted": True, "characters": 19, "preview": "Contr"}
    (derived,) = result.derived
    assert derived.slot == "texts" and derived.filename == "contracte.txt"
    assert derived.content == b"Contracte de servei"
    assert derived.content_type == "text/plain; charset=utf-8"
    metadata_only = await TextExtraction().process(document, opener(docx("x")))
    assert metadata_only.derived == [] and metadata_only.metadata["characters"] == 1
