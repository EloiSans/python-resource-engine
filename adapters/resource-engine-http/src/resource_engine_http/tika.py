"""Apache Tika server as a ``TextExtractor``: text from PDF, Office, OpenDocument,
e-mail, HTML, images (OCR, with Tesseract in the Tika image) and hundreds of
other formats.

```python
from resource_engine.documents.processors import TextExtraction
from resource_engine_http import TikaTextExtractor

TextExtraction(TikaTextExtractor(http, "http://tika:9998"), slot="texts")
```

The content is streamed to ``PUT /tika`` (text) after ``PUT /meta`` (page
count and language). Tika answers 415/422 for content it can't read: no text,
not an error. Other failures raise ``RemoteCallFailed`` and the document is
processed again later.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import httpx
from resource_engine.documents.processors import ExtractedText
from resource_engine.errors import RemoteCallFailed

DEFAULT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/vnd.ms-",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.oasis.opendocument.",
    "application/rtf",
    "application/epub+zip",
    "message/rfc822",
    "text/html",
    "image/",
)


class TikaTextExtractor:
    """``TextExtractor`` over a Tika server.

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (timeouts: large PDFs take time).
        url: The Tika server.
        types: Content types (or prefixes) sent to Tika.
        ocr: Read text in images and scanned PDFs (slower; the Tika image
            needs Tesseract, as the ``-full`` images have).
    """

    service = "tika"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str = "http://127.0.0.1:9998",
        *,
        types: Iterable[str] = DEFAULT_TYPES,
        ocr: bool = False,
    ) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.types = tuple(types)
        self.ocr = ocr

    def handles(self, content_type: str) -> bool:
        kind = content_type.split(";")[0].strip().lower()
        return any(
            kind == t or (t.endswith(("/", ".", "-")) and kind.startswith(t)) for t in self.types
        )

    def _headers(self, content_type: str, accept: str) -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": accept}
        if not self.ocr:
            headers["X-Tika-OCRskipOcr"] = "true"
        return headers

    async def _put(
        self, path: str, content: Callable[[], Any], headers: dict[str, str]
    ) -> httpx.Response | None:
        try:
            response = await self.client.put(
                f"{self.url}{path}", content=content(), headers=headers
            )
        except httpx.HTTPError as exc:
            raise RemoteCallFailed(
                f"Tika {path} failed: {exc}", service=self.service, operation=path.strip("/")
            ) from exc
        if response.status_code in (415, 422):  # a type or file Tika can't read
            return None
        if response.status_code >= 400:
            raise RemoteCallFailed(
                f"Tika {path} returned HTTP {response.status_code}",
                service=self.service,
                operation=path.strip("/"),
                status=response.status_code,
                body=response.text[:300],
            )
        return response

    async def extract(self, content: Callable[[], Any], content_type: str) -> ExtractedText | None:
        text = await self._put("/tika", content, self._headers(content_type, "text/plain"))
        if text is None:
            return None
        meta = await self._put("/meta", content, self._headers(content_type, "application/json"))
        data = meta.json() if meta is not None else {}
        pages = data.get("xmpTPg:NPages") or data.get("meta:page-count")
        language = data.get("language") or data.get("dc:language")
        return ExtractedText(
            text.text.strip(),
            pages=int(pages) if isinstance(pages, (str, int)) and str(pages).isdigit() else None,
            language=language if isinstance(language, str) else None,
        )


__all__ = ["TikaTextExtractor"]
