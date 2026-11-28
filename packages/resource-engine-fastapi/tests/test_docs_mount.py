"""mount_docs: the documentation site served by the app, with security headers."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from resource_engine_fastapi import mount_docs


async def test_the_site_is_served_with_security_headers(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Docs</title>")
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "index.html").write_text("<!doctype html><title>English</title>")
    app = FastAPI()
    mount_docs(app, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as client:
        root = await client.get("/documentation/")
        assert root.status_code == 200 and "<title>Docs</title>" in root.text
        english = await client.get("/documentation/en/")
        assert "English" in english.text
        assert "connect-src 'self'" in english.headers["content-security-policy"]
        assert english.headers["x-content-type-options"] == "nosniff"
        assert english.headers["referrer-policy"] == "no-referrer"
        assert (await client.get("/documentation/missing.html")).status_code == 404


def test_a_site_that_was_not_built_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="docs site"):
        mount_docs(FastAPI(), tmp_path / "site")


async def test_without_csp(tmp_path):
    (tmp_path / "index.html").write_text("ok")
    app = FastAPI()
    mount_docs(app, tmp_path, path="/docs-site", csp=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as client:
        page = await client.get("/docs-site/")
        assert page.status_code == 200 and "content-security-policy" not in page.headers
