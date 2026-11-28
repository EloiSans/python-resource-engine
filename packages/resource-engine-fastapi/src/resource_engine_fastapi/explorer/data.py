"""What the explorer's operations panel needs, from the OpenAPI document."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from resource_engine.schema_examples import example

METHODS = ("get", "post", "put", "patch", "delete")


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


__all__ = ["METHODS", "catalogue_action", "console_data", "example"]
