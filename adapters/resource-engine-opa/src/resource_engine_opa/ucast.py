"""UCAST (OPA compile API, ``application/vnd.opa.ucast.all+json``) → the
engine-neutral condition language of ``resource_engine.authz.conditions``.

Field ``resource.owner`` (unknown ``input.resource``) becomes the attribute
``resource.owner``; ``input.amount`` (unknown ``input.input``) becomes
``input.amount``. Operators outside the condition language (``startswith``,
``endswith``) raise ``ConditionNotSupported``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from resource_engine.authz.conditions import conjunction, disjunction, negation
from resource_engine.errors import ConditionNotSupported

_FIELD_OPS = {
    "eq": "eq",
    "ne": "ne",
    "lt": "lt",
    "lte": "le",
    "gt": "gt",
    "gte": "ge",
    "in": "in",
    "nin": "nin",
    "contains": "contains",
}


def ucast_condition(node: Any) -> Any:
    """Translate one UCAST node; ``{}`` (no condition) is ``True``."""
    if node in ({}, None):
        return True
    if not isinstance(node, Mapping):
        raise ConditionNotSupported(f"unexpected UCAST node {node!r}")
    kind = node.get("type")
    if kind is None and "operator" not in node:
        return _concise(node)
    op = node.get("operator")
    if kind == "compound":
        parts = [ucast_condition(n) for n in node.get("value") or ()]
        if op == "and":
            return conjunction(*parts)
        if op == "or":
            return disjunction(*parts)
        if op == "not":
            return negation(conjunction(*parts))
        raise ConditionNotSupported(f"UCAST compound operator {op!r}")
    if kind == "field":
        return _field(node["field"], op, node.get("value"))
    raise ConditionNotSupported(f"UCAST node type {kind!r}")


def _field(field: str, op: str | None, value: Any) -> Any:
    if op not in _FIELD_OPS:
        raise ConditionNotSupported(f"UCAST operator {op!r} on {field}")
    operand = {"attr": value["field"]} if isinstance(value, Mapping) and "field" in value else value
    return {_FIELD_OPS[op]: [{"attr": field}, operand]}


def _concise(node: Mapping[str, Any]) -> Any:
    """``{"t.a": 1, "t.b": {"in": [..]}}``: an AND of fields (eq by default)."""
    parts = []
    for field, spec in node.items():
        if isinstance(spec, Mapping) and spec and all(k in _FIELD_OPS for k in spec):
            parts.extend(_field(field, op, v) for op, v in spec.items())
        else:
            parts.append(_field(field, "eq", spec))
    return conjunction(*parts)
