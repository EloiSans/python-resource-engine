"""The static documentation site: pages, links, diagrams, events, search, escaping."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pytest
from invoices.bootstrap import authz, registry
from pydantic import BaseModel
from resource_engine import Resource, ResourceRegistry
from resource_engine.messaging.asyncapi import asyncapi_document
from resource_engine_devtools.cli import main
from resource_engine_devtools.documentation import introspect
from resource_engine_devtools.documentation.generators.site import render_site
from resource_engine_devtools.documentation.i18n import docs_locale


def _site(**kw):
    """The example's site in its own language (Catalan)."""
    kw.setdefault("locales", ["ca"])
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
    assert page.count('<pre class="mermaid">stateDiagram-v2') == 2  # approval and delivery
    assert page.count('<pre class="mermaid">flowchart LR') == 4  # operations with hooks
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
    # one copy per language of [tool.resource-engine.i18n], the root picks one
    for locale in ("ca", "en", "es"):
        assert (out / locale / "resources" / "invoices.html").is_file()
    assert "location.replace" in (out / "index.html").read_text()
    english = (out / "en" / "resources" / "invoices.html").read_text()
    assert '<html lang="en">' in english and "The organisation&#x27;s invoices." in english
    assert 'href="../../ca/resources/invoices.html" hreflang="ca"' in english  # the switcher
    spanish = (out / "es" / "resources" / "invoices.html").read_text()
    assert "Gestión de las facturas de la organización." in spanish and "Operaciones" in spanish
    asyncapi = json.loads((out / "ca" / "asyncapi.json").read_text())  # next to its pages
    assert asyncapi["asyncapi"].startswith("3.")
    assert (out / "en" / "api.html").is_file() and (out / "en" / "openapi.json").is_file()
    assert json.loads(capsys.readouterr().out)["index"].endswith("index.html")


def test_every_state_machine_on_one_page_linked_from_the_catalogue():
    files = _site()
    machines = files["state-machines.html"]
    assert machines.count('<pre class="mermaid">') == 5  # invoices ×2, payments, documents, keys
    for anchor in ("invoices-approval", "invoices-delivery", "invoice_payments-status"):
        assert f'id="{anchor}"' in machines
        assert f'href="state-machines.html#{anchor}"' in files["index.html"]
    assert 'href="state-machines.html"' in files["index.html"]  # the menu
    assert "import mermaid" in machines


def test_emails_are_listed_and_previewed_per_locale():
    from invoices.notifications import templates
    from resource_engine_devtools.documentation.generators.markdown import render_markdown

    model = introspect(registry, authz, templates=templates)
    [email] = model.emails
    assert email.template == "invoice_approved" and email.missing == []
    assert email.used_by == ["invoices after_transition:approve"]
    # one variant per catalogue locale, the source (default) language first
    assert [v.locale for v in email.variants] == ["ca", "en", "es"]
    catalan, english, spanish = email.variants
    assert catalan.subject == "Factura {{ number }} aprovada"
    assert english.subject == "Invoice {{ number }} approved"
    assert "Hello {{ customer }}," in english.text and "ha sido aprobada" in spanish.text
    assert '<mark class="placeholder">{{ customer }}</mark>' in catalan.html

    with docs_locale("ca"):
        pages = render_markdown(model)
    invoices = pages["resources/invoices.md"]
    assert "[`invoice_approved`](../emails.md#invoice_approved)" in invoices
    assert "`[customer.email]`" in invoices and "## Correus" in invoices
    assert "### en" in pages["emails.md"]

    site = render_site(model, locales=["ca"])
    preview = site["emails.html"]
    assert '<iframe class="email-preview" sandbox=""' in preview
    assert "&lt;mark class=&quot;placeholder&quot;&gt;" in preview  # escaped into srcdoc
    assert 'href="emails.html"' in site["index.html"]


def test_hooks_are_described_per_operation_in_lifecycle_order():
    from invoices.notifications import templates
    from resource_engine_devtools.documentation.generators.markdown import render_markdown

    with docs_locale("ca"):
        pages = render_markdown(introspect(registry, authz, templates=templates))
    page = pages["resources/invoices.md"]
    section = page.split("## Què passa en cada operació")[1].split("## Schemas")[0]
    approve = section.split("### Transició `approve`")[1].split("###")[0]
    steps = re.findall(r"^\d+\. (.+)$", approve, re.MULTILINE)
    assert steps[0].startswith("Es valida l'entrada")
    assert steps[1] == "Cal un document disponible a `signed_pdf`."
    assert steps[2] == "Canvia l'estat."
    assert steps[3].startswith("Abans de confirmar: `request_approval_email`")
    assert steps[4].startswith("Es confirma el canvi")
    assert steps[5].startswith("Després de confirmar: s'envia el correu")
    assert 's4{{"Confirmar"}}' in approve  # the commit in the diagram
    update = section.split("### Modificar (`update`)")[1].split("###")[0]
    assert "Abans de desar: `ensure_editable`" in update
    assert "Abans de confirmar: `validate_amount`" in update
    assert "### Transició `submit`" not in section  # nothing of its own to show
    assert "Després de qualsevol canvi confirmat" in section and "`notify_approval`" in section


def test_the_audience_decides_the_technical_content():
    from graphql import GraphQLField, GraphQLObjectType, GraphQLSchema, GraphQLString, print_schema

    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "Factures", "version": "1.0"},
        "paths": {
            "/invoices": {
                "post": {
                    "tags": ["invoices"],
                    "summary": "Crea una factura.",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/InvoiceCreate"}
                            }
                        }
                    },
                    "responses": {"201": {"content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/InvoiceRead"}}}}},
                }
            }
        },
        "components": {"schemas": {"InvoiceCreate": {
            "properties": {"amount": {"type": "string", "description": "Import"}},
            "required": ["amount"],
        }}},
    }  # fmt: skip
    sdl = print_schema(
        GraphQLSchema(GraphQLObjectType("Query", {"hello": GraphQLField(GraphQLString)}))
    )
    technical = _site(openapi=openapi, graphql=sdl)
    assert {"api.html", "graphql.html", "openapi.json", "asyncapi.json", "schema.graphql"} <= set(
        technical
    )
    api = technical["api.html"]
    assert "<code>/invoices</code>" in api and "<code>InvoiceCreate</code>" in api
    assert "Import" in api and 'href="openapi.json"' in api
    assert "Dades dels missatges" in technical["events.html"]  # payload schemas
    assert (
        'href="api.html"' in technical["index.html"] and "type Query" in technical["graphql.html"]
    )
    assert "invoices.application.resource.InvoiceResource" in technical["resources/invoices.html"]

    functional = _site(openapi=openapi, graphql=sdl, audience="functional")
    assert not {"api.html", "graphql.html", "openapi.json", "asyncapi.json"} & set(functional)
    page = functional["resources/invoices.html"]
    assert "InvoiceResource" not in page and "/invoices/{id}" not in page
    assert "InvoiceCreate" not in page and "Esquemes" not in page  # no schemas
    assert "Rebutja factures amb import negatiu." in page  # what the hooks do stays
    assert "validate_amount" not in page  # their function names don't
    assert "events.html" in functional and "Dades dels missatges" not in functional["events.html"]


def test_cli_audience_from_the_option_and_the_config(tmp_path):
    out = tmp_path / "f"
    assert main(["docs", "generate", "--out", str(out), "--audience", "functional"]) == 0
    assert not (out / "asyncapi.json").exists() and not (out / "api.md").exists()
    technical = tmp_path / "t"
    assert main(["docs", "generate", "--out", str(technical)]) == 0  # the default
    assert (technical / "api.md").is_file() and (technical / "openapi.json").is_file()
    assert json.loads((technical / "openapi.json").read_text())["paths"]


def test_console_data_and_pages():
    from invoices.app import create_app
    from resource_engine_fastapi.explorer.data import catalogue_action, console_data, example

    assert catalogue_action("invoices", "GET", "/invoices") == "invoices:list"
    assert catalogue_action("invoices", "POST", "/invoices") == "invoices:create"
    assert catalogue_action("invoices", "PATCH", "/invoices/{entity_id}") == "invoices:update"
    assert catalogue_action("invoices", "DELETE", "/invoices/{entity_id}") == "invoices:delete"
    assert (
        catalogue_action("invoices", "POST", "/invoices/{entity_id}/transitions/approve")
        == "invoices:transition:approve"
    )
    assert (
        catalogue_action("invoices", "POST", "/invoices/actions/export") == "invoices:action:export"
    )
    assert catalogue_action("invoices", "GET", "/invoices/{entity_id}/history") is None
    assert catalogue_action("invoices", "GET", "/invoices/export") == "invoices:list"
    assert catalogue_action("invoices", "POST", "/invoices/exports") == "invoices:list"
    assert (
        catalogue_action("invoices", "GET", "/invoices/{entity_id}/printouts/invoice")
        == "invoices:get"
    )
    schemas = {"Thing": {"properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                         "required": ["b"]}}  # fmt: skip
    assert example({"$ref": "#/components/schemas/Thing"}, schemas) == {"b": 0}
    assert example({"properties": {"when": {"format": "date"}}}, {}) == {"when": "2026-01-31"}

    openapi = create_app().openapi()
    data = console_data(openapi, ["invoices"])
    ops = {(o["method"], o["path"]): o for o in data["resources"]["invoices"]}
    create = ops[("POST", "/invoices")]
    assert create["action"] == "invoices:create" and create["body"] == {"customer": "", "amount": 0}
    reject = ops[("POST", "/invoices/{entity_id}/transitions/reject")]
    assert reject["body"] == {"reason": ""}
    assert {p["name"] for p in reject["params"]} >= {"entity_id", "If-Match", "Idempotency-Key"}
    assert ops[("GET", "/invoices/export")]["download"] is True  # a file: a download link
    assert create["download"] is False and create["safe"] is False

    files = _site(openapi=openapi, console=True, api_url="https://api.example.com")
    script = files["assets/console.js"]
    payload = json.loads(script.split("window.CONSOLE = ", 1)[1].split(";\n", 1)[0])
    assert payload["api"] == "https://api.example.com" and payload["me"] is True
    assert payload["labels"]["try_it"] == "Prova-ho"  # the page's language
    listing = next(o for o in payload["resources"]["invoices"] if o["path"] == "/invoices")
    assert listing["summary"] == "Llista les factures, filtrables per estat i client."
    page = files["resources/invoices.html"]
    assert 'data-resource="invoices"' in page and 'src="../assets/console.js"' in page
    assert 'data-page="index"' in files["index.html"]
    assert "console.js" not in _site(openapi=openapi)["resources/invoices.html"]
    with pytest.raises(ValueError):
        _site(console=True)  # no OpenAPI to build it from


def test_the_events_page_gets_the_live_panel_when_the_app_has_it():
    from invoices.app import create_app

    openapi = create_app().openapi()
    plain = _site(openapi=openapi, console=True)
    assert '"events"' not in plain["assets/console.js"].split("\n", 1)[0]
    openapi["paths"]["/_explorer/events/stream"] = {
        "get": {"operationId": "explorer_event_stream", "tags": ["explorer"], "responses": {}}
    }
    files = _site(openapi=openapi, console=True)
    payload = json.loads(
        files["assets/console.js"].split("window.CONSOLE = ", 1)[1].split(";\n", 1)[0]
    )
    events = payload["events"]
    assert events["stream"] == "/_explorer/events/stream" and events["deliveries"] is None
    assert "invoices" in events["resources"] and events["types"]
    assert payload["labels"]["connect"] == "Connecta"  # the explorer's catalogue, in Catalan
    assert "R.panels.events = panel" in files["assets/console.js"]
    assert 'data-page="events"' in files["events.html"]


def test_cli_console_needs_the_app(tmp_path, monkeypatch, capsys):
    out = tmp_path / "site"
    assert main(["docs", "site", "--out", str(out), "--console", "--locale", "en"]) == 0
    assert (out / "assets" / "console.js").is_file()
    monkeypatch.chdir(tmp_path)  # no pyproject: no app
    capsys.readouterr()
    args = ["docs", "site", "--out", str(out), "--console", "--app", "invoices.bootstrap:registry"]
    assert main(args) == 2
    assert "needs the application" in capsys.readouterr().out
