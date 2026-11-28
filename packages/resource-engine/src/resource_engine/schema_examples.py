"""Example values from JSON Schemas: a starting point to edit in a form (the
explorer's request bodies and event payloads)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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


__all__ = ["example"]
