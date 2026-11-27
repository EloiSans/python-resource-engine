"""Serve the documentation site from the application (the visual explorer).

```bash
uv run resource-engine docs site --out site --console   # at build time
```

```python
from resource_engine_fastapi import mount_docs

mount_docs(app, "site")   # http://localhost:8000/explorer/
```

The site is static files built by ``resource-engine docs site``; with
``--console`` each resource page gets a try-it panel that calls this same API
with the visitor's own token, so it sees exactly what that user may see and
do. Nothing here needs devtools at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

#: Pages may run their own scripts and Mermaid from its CDN; they only call this
#: origin; email previews are sandboxed frames.
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
    "frame-src 'self' about:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
)


class _DocsFiles(StaticFiles):
    """Static files with security headers (the console handles tokens)."""

    def __init__(self, *, directory: str, csp: str | None) -> None:
        super().__init__(directory=directory, html=True)
        self.csp = csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                if self.csp:
                    headers.append((b"content-security-policy", self.csp.encode()))
                message["headers"] = headers
            await send(message)

        await super().__call__(scope, receive, with_headers)


def mount_docs(
    app: Any, directory: str | Path, *, path: str = "/explorer", csp: str | None = CSP
) -> None:
    """Serve the site in ``directory`` at ``path`` (``/explorer/``).

    ``csp``: the Content-Security-Policy of the pages; the default allows
    Mermaid from its CDN and API calls to this origin only. Pass your own when
    the site uses a self-hosted Mermaid or calls an API elsewhere, or ``None``.
    """
    folder = Path(directory)
    if not (folder / "index.html").is_file():
        raise FileNotFoundError(
            f"{folder}/index.html not found: build it with `resource-engine docs site --out "
            f"{folder} --console`"
        )
    app.mount(path, _DocsFiles(directory=str(folder), csp=csp), name="docs-site")


__all__ = ["CSP", "mount_docs"]
