"""The application's machine-readable API documents next to the functional
documentation: ``openapi.json`` (HTTP), ``asyncapi.json`` (events) and
``schema.graphql``, linked from the home page. The documentation explains
the application; to look at the API or try it, use these files or the
explorer (``mount_explorer``, docs/explorer.md).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from resource_engine_devtools.documentation.i18n import t

#: The documents, in the order they are listed.
DOCUMENTS = ("openapi.json", "asyncapi.json", "schema.graphql")


def _label(name: str) -> str:
    return {
        "openapi.json": t("OpenAPI: the HTTP API"),
        "asyncapi.json": t("AsyncAPI: the published and consumed events"),
        "schema.graphql": t("GraphQL schema"),
    }[name]


def api_pages(
    asyncapi: Mapping[str, Any] | None = None,
    openapi: Mapping[str, Any] | None = None,
    graphql: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """``(pages, documents)``: the events page, and the API documents to
    write next to the pages."""
    from resource_engine_devtools.documentation.generators.site import render_events

    pages: dict[str, str] = {}
    documents: dict[str, str] = {}
    if asyncapi and asyncapi.get("channels"):
        pages["events.md"] = render_events(asyncapi)
        documents["asyncapi.json"] = _json(asyncapi)
    if openapi:
        documents["openapi.json"] = _json(openapi)
    if graphql:
        documents["schema.graphql"] = graphql.rstrip("\n") + "\n"
    return pages, documents


def documents_section(documents: Mapping[str, str]) -> str:
    """The home page's list of the API documents ("" without any)."""
    names = [name for name in DOCUMENTS if name in documents]
    if not names:
        return ""
    lines = ["", f"## {t('API documents')}", "", t("Machine-readable descriptions of the API:"), ""]
    lines += [f"- [`{name}`]({name}): {_label(name)}" for name in names]
    return "\n".join(lines) + "\n"


def _json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


__all__ = ["DOCUMENTS", "api_pages", "documents_section"]
