"""The static documentation site: pages, links, diagrams, events, search, escaping."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from invoices.bootstrap import authz, registry
from pydantic import BaseModel
from resource_engine import Resource, ResourceRegistry
from resource_engine.messaging.asyncapi import asyncapi_document
from resource_engine_devtools.cli import main
from resource_engine_devtools.documentation import introspect
from resource_engine_devtools.documentation.generators.site import render_site


def _site(**kw):
    return render_site(introspect(registry, authz), asyncapi=asyncapi_document(registry), **kw)


def test_every_page_with_navigation_and_links():
    files = _site(title="Factures")
    assert {"index.html", "permissions.html", "events.html", "resources/invoices.html"} <= set(
        files
    )
    page = files["resources/invoices.html"]
    assert "<title>invoices · Factures</title>" in page
    assert 'href="../assets/site.css"' in page and 'data-root="../"' in page
    assert 'href="../resources/customers.html"' in page  # the menu
    assert re.search(
        r'href="\.\./resources/invoices\.html" aria-current="page"[^>]*>invoices<', page
    )
    assert ".md" not in files["index.html"].split("<main>")[1]  # links point at HTML pages
    assert 'href="resources/invoices.html"' in files["index.html"]
    assert '<h2 id="operacions">' in page and "<table>" in page


def test_diagrams_are_drawn_in_the_browser():
    page = _site()["resources/invoices.html"]
    assert page.count('<pre class="mermaid">') == 2  # approval and delivery
    assert "stateDiagram-v2" in page and "draft --&gt; pending: submit" in page
    assert "mermaid@11" in page
    quiet = _site(mermaid_url="")["resources/invoices.html"]
    assert '<pre class="mermaid">' in quiet and "import mermaid" not in quiet
    assert "import mermaid" not in _site()["index.html"]  # only where there are diagrams


def test_events_page_and_search_index():
    files = _site()
    events = files["events.html"]
    assert '<h2 id="invoices">invoices</h2>' in events
    assert "TransitionExecuted" in events and "approval, delivery" in events
    script = files["assets/site.js"]
    index = json.loads(script.split("window.SITE_SEARCH = ", 1)[1].split(";\n", 1)[0])
    paths = {entry["path"] for entry in index}
    assert "resources/invoices.html" in paths and "events.html" in paths
    assert files["assets/site.css"].startswith(":root")


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteRead(BaseModel):
    id: int
    text: str


class NoteResource(Resource):
    """Notes <script>alert('x')</script> with **markup**."""

    name = "notes"
    model = Note
    schemas = {"read": NoteRead}
    operations = ["get"]


def test_documentation_text_is_escaped():
    reg = ResourceRegistry()
    reg.register(NoteResource)
    page = render_site(introspect(reg))["resources/notes.html"]
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page
    assert "<strong>markup</strong>" in page  # Markdown still renders


def test_cli_writes_the_site(tmp_path, capsys):
    out = tmp_path / "site"
    args = ["docs", "site", "--out", str(out), "--app", "invoices.bootstrap:registry", "--json"]
    assert main(args) == 0
    assert (out / "index.html").is_file() and (out / "resources" / "invoices.html").is_file()
    assert json.loads((out / "asyncapi.json").read_text())["asyncapi"].startswith("3.")
    assert json.loads(capsys.readouterr().out)["index"].endswith("index.html")
