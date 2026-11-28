"""Technical pages of the documentation: the HTTP API (OpenAPI), the events'
payloads (AsyncAPI) and the GraphQL schema, for the ``technical`` audience.

Each page is Markdown like the rest, and links the machine-readable document
written next to it (``openapi.json``, ``asyncapi.json``, ``schema.graphql``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from resource_engine_devtools.documentation.i18n import t

METHODS = ("get", "post", "put", "patch", "delete")


def _cell(text: Any) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _ref(schema: Mapping[str, Any] | None) -> str:
    """A schema as a short name: ``InvoiceRead``, ``list[InvoiceRead]``, ``string``."""
    if not schema:
        return "—"
    if "$ref" in schema:
        return f"`{schema['$ref'].rsplit('/', 1)[-1]}`"
    if schema.get("type") == "array":
        return f"list[{_ref(schema.get('items'))}]"
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return " \\| ".join(_ref(s) for s in schema[key])
    return f"`{schema.get('type', 'object')}`"


def _body(operation: Mapping[str, Any]) -> str:
    content = operation.get("requestBody", {}).get("content", {})
    for media, spec in content.items():
        name = _ref(spec.get("schema"))
        return name if media == "application/json" else f"{name} ({media})"
    return "—"


def _responses(operation: Mapping[str, Any]) -> str:
    out = []
    for status, spec in operation.get("responses", {}).items():
        schema = spec.get("content", {}).get("application/json", {}).get("schema")
        out.append(f"{status} {_ref(schema)}" if schema else str(status))
    return ", ".join(out) or "—"


def render_openapi(openapi: Mapping[str, Any]) -> str:
    """The HTTP API as a page: one table per tag, then the schemas."""
    info = openapi.get("info", {})
    out = [
        f"# {t('HTTP API')}",
        "",
        t(
            "{title} {version}, from the OpenAPI document of the application: "
            "[`openapi.json`](openapi.json).",
            title=info.get("title", ""),
            version=info.get("version", ""),
        ),
    ]
    by_tag: dict[str, list[tuple[str, str, Mapping[str, Any]]]] = {}
    for path, item in openapi.get("paths", {}).items():
        for method in METHODS:
            if method in item:
                operation = item[method]
                tag = (operation.get("tags") or [t("Other")])[0]
                by_tag.setdefault(tag, []).append((method.upper(), path, operation))
    for tag in sorted(by_tag):
        out += [
            "",
            f"## {tag}",
            "",
            f"| {t('Method')} | {t('Path')} | {t('Summary')} | {t('Body')} | {t('Responses')} |",
            "|---|---|---|---|---|",
        ]
        for method, path, operation in by_tag[tag]:
            out.append(
                f"| {method} | `{path}` | {_cell(operation.get('summary'))} "
                f"| {_body(operation)} | {_responses(operation)} |"
            )
    schemas = openapi.get("components", {}).get("schemas", {})
    if schemas:
        out += ["", f"## {t('Schemas')}"]
        for name in sorted(schemas):
            schema = schemas[name]
            required = set(schema.get("required", []))
            properties = schema.get("properties", {})
            out += ["", f"### {name}", ""]
            if schema.get("description"):
                out += [_cell(schema["description"]), ""]
            if not properties:
                out += ["```json", json.dumps(schema, indent=2, ensure_ascii=False), "```"]
                continue
            out += [
                f"| {t('Field')} | {t('Type')} | {t('Required')} | {t('Description')} |",
                "|---|---|---|---|",
            ]
            for field, spec in properties.items():
                out.append(
                    f"| `{field}` | {_ref(spec)} | {t('yes') if field in required else t('no')} "
                    f"| {_cell(spec.get('description') or spec.get('title'))} |"
                )
    return "\n".join(out) + "\n"


def render_asyncapi_payloads(asyncapi: Mapping[str, Any]) -> str:
    """Each published message's payload schema, after the events page."""
    components = asyncapi.get("components", {})
    out = [
        "",
        f"## {t('Payloads')}",
        "",
        t("The JSON schema of each message, from [`asyncapi.json`](asyncapi.json)."),
    ]
    for name, message in sorted(components.get("messages", {}).items()):
        payload = message.get("payload", {})
        if "$ref" in payload:
            payload = components.get("schemas", {}).get(payload["$ref"].rsplit("/", 1)[-1], {})
        out += [
            "",
            f"### {name}",
            "",
            "```json",
            json.dumps(payload, indent=2, ensure_ascii=False),
            "```",
        ]
    return "\n".join(out) + "\n"


def render_graphql(sdl: str) -> str:
    return (
        "\n".join(
            [
                f"# {t('GraphQL schema')}",
                "",
                t("The schema the application serves: [`schema.graphql`](schema.graphql)."),
                "",
                "```graphql",
                sdl.rstrip("\n"),
                "```",
            ]
        )
        + "\n"
    )


# ---------------------------------------------------------------- console


def console_data(openapi: Mapping[str, Any], resources: list[str]) -> dict[str, Any]:
    """What the try-it console needs, per resource: each operation's method,
    path, parameters, a JSON body to start from, and its permission action
    (for ``/authz/explain``)."""
    schemas = openapi.get("components", {}).get("schemas", {})
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in resources}
    for path, item in openapi.get("paths", {}).items():
        for method in METHODS:
            if method not in item:
                continue
            operation = item[method]
            resource = (operation.get("tags") or [None])[0]
            if resource not in out:
                continue
            body = None
            content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                body = example(content["application/json"].get("schema", {}), schemas)
            ok = operation.get("responses", {}).get("200", {}).get("content", {})
            out[resource].append(
                {
                    # a file, not JSON: the console offers it for download
                    "download": bool(ok) and "application/json" not in ok,
                    # starting an export changes no data
                    "safe": method == "post" and path.endswith("/exports"),
                    "method": method.upper(),
                    "path": path,
                    "summary": operation.get("summary") or "",
                    "action": catalogue_action(resource, method.upper(), path),
                    "params": [
                        {
                            "name": p["name"],
                            "in": p["in"],
                            "required": bool(p.get("required")),
                            "description": p.get("description") or "",
                        }
                        for p in operation.get("parameters", [])
                        if p["in"] in ("path", "query", "header")
                    ],
                    "body": body,
                    "upload": "multipart/form-data" in content,
                }
            )
    return {"resources": out}


def catalogue_action(resource: str, method: str, path: str) -> str | None:
    """The permission action of a route: ``POST /invoices/{id}/transitions/approve``
    → ``invoices:transition:approve``."""
    parts = [p for p in path.strip("/").split("/") if p]
    if not parts or parts[0] != resource:
        return None
    rest = parts[1:]
    if not rest:
        return f"{resource}:{'list' if method == 'GET' else 'create'}"
    if rest in (["export"], ["exports"]):
        return f"{resource}:list"
    if len(rest) == 3 and rest[1] == "printouts":
        return f"{resource}:get"
    if len(rest) == 1 and rest[0].startswith("{"):
        return f"{resource}:" + {"GET": "get", "PATCH": "update", "PUT": "update"}.get(
            method, "delete"
        )
    if len(rest) >= 3 and rest[1] in ("transitions", "actions"):
        kind = "transition" if rest[1] == "transitions" else "action"
        return f"{resource}:{kind}:{rest[2]}"
    if len(rest) == 2 and rest[0] == "actions":
        return f"{resource}:action:{rest[1]}"
    return None


def example(schema: Mapping[str, Any], schemas: Mapping[str, Any], depth: int = 0) -> Any:
    """A value to start editing from: the required fields (or, when none is,
    the first one as a hint), empty strings, zeros, the first enum value."""
    if "$ref" in schema:
        return example(schemas.get(schema["$ref"].rsplit("/", 1)[-1], {}), schemas, depth)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"]
            return example(options[0], schemas, depth) if options else None
    if "allOf" in schema:
        return example(schema["allOf"][0], schemas, depth)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        if depth > 2:
            return {}
        properties = schema.get("properties", {})
        names = [n for n in properties if n in schema.get("required", [])]
        names = names or list(properties)[:1]
        return {n: example(properties[n], schemas, depth + 1) for n in names}
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0
    if kind == "boolean":
        return False
    fmt = schema.get("format")
    if fmt == "date":
        return "2026-01-31"
    if fmt == "date-time":
        return "2026-01-31T10:00:00Z"
    return ""


def api_pages(
    asyncapi: Mapping[str, Any] | None = None,
    openapi: Mapping[str, Any] | None = None,
    graphql: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """``(pages, documents)``: the events page (and, for the technical audience,
    the payloads, the HTTP API and the GraphQL schema), and the machine-readable
    documents to write next to them (technical audience only)."""
    from resource_engine_devtools.documentation.audience import technical
    from resource_engine_devtools.documentation.generators.site import render_events

    pages: dict[str, str] = {}
    documents: dict[str, str] = {}
    if asyncapi and asyncapi.get("channels"):
        pages["events.md"] = render_events(asyncapi)
        if technical():
            pages["events.md"] += render_asyncapi_payloads(asyncapi)
            documents["asyncapi.json"] = _json(asyncapi)
    if technical() and openapi:
        pages["api.md"] = render_openapi(openapi)
        documents["openapi.json"] = _json(openapi)
    if technical() and graphql:
        pages["graphql.md"] = render_graphql(graphql)
        documents["schema.graphql"] = graphql.rstrip("\n") + "\n"
    return pages, documents


def _json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


__all__ = [
    "api_pages",
    "catalogue_action",
    "console_data",
    "example",
    "render_asyncapi_payloads",
    "render_graphql",
    "render_openapi",
]
