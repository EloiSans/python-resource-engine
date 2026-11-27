"""HTML to PDF with Gotenberg (headless Chromium behind an HTTP API), the
``PdfConverter`` of generated documents.

```python
from resource_engine.generation import DocumentGenerator
from resource_engine_http import GotenbergPdfConverter

generator = DocumentGenerator(templates, pdf=GotenbergPdfConverter(http, "http://gotenberg:3000"))
```

Run Gotenberg next to the app (``gotenberg/gotenberg:8``); it's in
``compose.emulators.yaml`` on port 3010. Only the HTML is sent: no network
access is needed for templates whose styles and images are inline (data URLs).
"""

from __future__ import annotations

import httpx
from resource_engine.calls import outgoing_call

from resource_engine_http.errors import check_response, transport_failed


class GotenbergPdfConverter:
    """``PdfConverter`` over Gotenberg's Chromium route.

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (timeouts: printing takes
            seconds).
        url: Gotenberg's base URL.
        paper: ``(width, height)`` in inches (A4 by default).
        margins: Top, right, bottom and left margins in inches.
        print_background: Print CSS backgrounds.
    """

    service = "gotenberg"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        paper: tuple[float, float] = (8.27, 11.7),
        margins: tuple[float, float, float, float] = (0.6, 0.6, 0.6, 0.6),
        print_background: bool = True,
    ) -> None:
        self.client = client
        self.url = url.rstrip("/") + "/forms/chromium/convert/html"
        self.paper = paper
        self.margins = margins
        self.print_background = print_background

    async def html_to_pdf(self, html: str) -> bytes:
        top, right, bottom, left = self.margins
        fields = {
            "paperWidth": str(self.paper[0]),
            "paperHeight": str(self.paper[1]),
            "marginTop": str(top),
            "marginRight": str(right),
            "marginBottom": str(bottom),
            "marginLeft": str(left),
            "printBackground": "true" if self.print_background else "false",
        }
        files = {"files": ("index.html", html.encode("utf-8"), "text/html")}
        try:
            async with outgoing_call(self.service, "html_to_pdf", method="POST", url=self.url) as c:
                response = await self.client.post(self.url, data=fields, files=files)
                c.status = response.status_code
        except httpx.HTTPError as exc:
            raise transport_failed(exc, service=self.service, operation="html_to_pdf") from exc
        check_response(response, service=self.service, operation="html_to_pdf")
        return response.content


__all__ = ["GotenbergPdfConverter"]
