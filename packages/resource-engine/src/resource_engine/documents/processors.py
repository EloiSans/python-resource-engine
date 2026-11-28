"""Reference document processors for the processing pipeline.

```python
from resource_engine.documents.pipeline import DocumentPipeline
from resource_engine.documents.processors import ContentTypeCheck, TextExtraction
from resource_engine_clamav import ClamAvScanner
from resource_engine_http import TikaTextExtractor

DocumentPipeline(manager.documents, [
    ClamAvScanner("clamav", 3310),                      # first: nothing else reads malware
    ContentTypeCheck(),                                 # the bytes match the declared type
    TextExtraction(TikaTextExtractor(http, "http://tika:9998"), slot="texts"),
]).register(router)
```

- ``ContentTypeCheck`` reads the first bytes and rejects documents whose
  content is not what their declared type says (an executable uploaded as
  ``image/png``). It knows the common signatures (PDF, PNG, JPEG, GIF, WebP,
  TIFF, ZIP and Office files, Windows and Linux executables).
- ``TextExtraction`` stores the document's text as a derived ``text/plain``
  document (in ``slot``) and its length, page count and language as metadata,
  for search or classification. Text comes from a ``TextExtractor``:
  ``BuiltinTextExtractor`` reads plain text, CSV, JSON, XML and Word
  (``.docx``) files without dependencies; Apache Tika (``resource-engine-http``)
  reads PDF, Office, images (OCR) and hundreds more.
- Antivirus: ``ClamAvScanner`` (``resource-engine-clamav``).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from xml.etree import ElementTree

from resource_engine.documents.model import DocumentRead, NewDocument
from resource_engine.documents.pipeline import OpenContent, ProcessingResult

# ------------------------------------------------------------ content types

#: (signature, offset, detected type). ZIP covers Office Open XML and ODF.
SIGNATURES: tuple[tuple[bytes, int, str], ...] = (
    (b"%PDF-", 0, "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"GIF87a", 0, "image/gif"),
    (b"GIF89a", 0, "image/gif"),
    (b"WEBP", 8, "image/webp"),
    (b"II*\x00", 0, "image/tiff"),
    (b"MM\x00*", 0, "image/tiff"),
    (b"PK\x03\x04", 0, "application/zip"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "application/x-ole-storage"),
    (b"MZ", 0, "application/x-msdownload"),
    (b"\x7fELF", 0, "application/x-executable"),
)

#: Declared types each detected type may stand for.
COMPATIBLE: dict[str, tuple[str, ...]] = {
    "application/zip": (
        "application/zip",
        "application/vnd.openxmlformats-officedocument.",
        "application/vnd.oasis.opendocument.",
        "application/epub+zip",
        "application/java-archive",
    ),
    "application/x-ole-storage": (
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.ms-outlook",
    ),
}

EXECUTABLES = frozenset({"application/x-msdownload", "application/x-executable"})


def detect(head: bytes) -> str | None:
    """The type the first bytes show (``None``: no known signature, e.g. text)."""
    for signature, offset, kind in SIGNATURES:
        if head[offset : offset + len(signature)] == signature:
            return kind
    return None


async def read_head(content: OpenContent, size: int = 64) -> bytes:
    head = b""
    stream = content()
    try:
        async for chunk in stream:
            head += chunk
            if len(head) >= size:
                break
    finally:
        closing = getattr(stream, "aclose", None)
        if closing is not None:
            await closing()
    return head[:size]


class ContentTypeCheck:
    """Rejects documents whose bytes don't match their declared type.

    Args:
        allow_unknown: Accept content with no known signature (text, CSV,
            JSON...) whatever its declared type, unless that type is one with
            a signature (a "PDF" that doesn't start with ``%PDF-``).
    """

    name = "content_type"

    def __init__(self, *, allow_unknown: bool = True) -> None:
        self.allow_unknown = allow_unknown

    def accepts(self, document: DocumentRead) -> bool:
        return True

    async def process(self, document: DocumentRead, content: OpenContent) -> ProcessingResult:
        declared = document.content_type.split(";")[0].strip().lower()
        detected = detect(await read_head(content))
        if detected in EXECUTABLES:
            return ProcessingResult(reject=f"the content is an executable ({detected})")
        if detected is None:
            claimed = {kind for _, _, kind in SIGNATURES}
            if declared in claimed or not self.allow_unknown:
                return ProcessingResult(reject=f"the content is not {declared}")
            return ProcessingResult(metadata={"declared": declared, "detected": None})
        allowed = COMPATIBLE.get(detected, (detected,))
        if not any(declared == a or (a.endswith(".") and declared.startswith(a)) for a in allowed):
            return ProcessingResult(reject=f"declared {declared}, but the content is {detected}")
        return ProcessingResult(metadata={"declared": declared, "detected": detected})


# --------------------------------------------------------------------- text


@dataclass(frozen=True)
class ExtractedText:
    text: str
    pages: int | None = None
    language: str | None = None


class TextExtractor(Protocol):
    """Turns a document's content into text (``None``: not a type it reads)."""

    def handles(self, content_type: str) -> bool: ...

    async def extract(self, content: OpenContent, content_type: str) -> ExtractedText | None: ...


async def read_all(content: OpenContent, max_bytes: int) -> bytes:
    data = bytearray()
    async for chunk in content():
        data += chunk
        if len(data) > max_bytes:
            raise ValueError(f"more than {max_bytes} bytes")
    return bytes(data)


_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/csv")
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{_WORD}p"):
        paragraphs.append("".join(t.text or "" for t in paragraph.iter(f"{_WORD}t")))
    return "\n".join(paragraphs)


class BuiltinTextExtractor:
    """Plain text, CSV, JSON, XML and Word (``.docx``), with no dependencies.

    Args:
        max_bytes: Larger documents are skipped (no text).
    """

    def __init__(self, *, max_bytes: int = 20 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes

    def handles(self, content_type: str) -> bool:
        kind = content_type.split(";")[0].strip().lower()
        return kind.startswith(_TEXT_TYPES) or kind == _DOCX

    async def extract(self, content: OpenContent, content_type: str) -> ExtractedText | None:
        try:
            data = await read_all(content, self.max_bytes)
        except ValueError:
            return None
        kind = content_type.split(";")[0].strip().lower()
        if kind == _DOCX:
            try:
                return ExtractedText(_docx_text(data))
            except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
                return None
        charset = re.search(r"charset=([\w-]+)", content_type)
        for encoding in (charset.group(1) if charset else "utf-8", "utf-8-sig", "latin-1"):
            try:
                return ExtractedText(data.decode(encoding))
            except (UnicodeDecodeError, LookupError):
                continue
        return None  # pragma: no cover - latin-1 decodes anything


class TextExtraction:
    """Extracts each document's text: a derived ``text/plain`` document in
    ``slot`` (when given) and ``characters``, ``pages``, ``language`` as metadata.

    Args:
        extractors: Tried in order; the first that handles the type is used.
        slot: Slot of the owner for the text document (``None``: metadata only).
        preview: Characters of the text kept in the metadata (``0``: none).
        accepts: Which documents to read (default: every type an extractor handles).
    """

    name = "text"

    def __init__(
        self,
        *extractors: TextExtractor,
        slot: str | None = None,
        preview: int = 0,
        accepts: Callable[[DocumentRead], bool] | None = None,
    ) -> None:
        self.extractors: Sequence[TextExtractor] = extractors or (BuiltinTextExtractor(),)
        self.slot = slot
        self.preview = preview
        self._accepts = accepts

    def _extractor(self, content_type: str) -> TextExtractor | None:
        return next((e for e in self.extractors if e.handles(content_type)), None)

    def accepts(self, document: DocumentRead) -> bool:
        if self.slot is not None and document.slot == self.slot:
            return False  # never extract the extracted text again
        if self._accepts is not None:
            return self._accepts(document)
        return self._extractor(document.content_type) is not None

    async def process(self, document: DocumentRead, content: OpenContent) -> ProcessingResult:
        extractor = self._extractor(document.content_type)
        if extractor is None:
            return ProcessingResult()
        found = await extractor.extract(content, document.content_type)
        if found is None:
            return ProcessingResult(metadata={"extracted": False})
        metadata: dict[str, Any] = {"extracted": True, "characters": len(found.text)}
        if found.pages is not None:
            metadata["pages"] = found.pages
        if found.language:
            metadata["language"] = found.language
        if self.preview:
            metadata["preview"] = found.text[: self.preview]
        derived = []
        if self.slot is not None and found.text.strip():
            stem = document.filename.rsplit(".", 1)[0] or "document"
            derived.append(
                NewDocument(
                    slot=self.slot,
                    content=found.text.encode("utf-8"),
                    filename=f"{stem}.txt",
                    content_type="text/plain; charset=utf-8",
                )
            )
        return ProcessingResult(metadata=metadata, derived=derived)


async def chunks(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    """``data`` as an async stream (tests, extractors that buffer)."""
    for start in range(0, len(data), size):
        yield data[start : start + size]


__all__ = [
    "BuiltinTextExtractor",
    "ContentTypeCheck",
    "ExtractedText",
    "TextExtraction",
    "TextExtractor",
    "chunks",
    "detect",
    "read_all",
    "read_head",
]
