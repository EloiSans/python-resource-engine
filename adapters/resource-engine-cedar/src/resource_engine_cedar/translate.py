"""Cedar residual policies (JSON policy format) → the engine-neutral condition
language of ``resource_engine.authz.conditions``.

Supported: ``== != < <= > >= && || !``, ``has``, ``contains``, ``in`` with a
set, ``is``, attribute access on the unknown ``resource`` and on
``context.input``, the decimal comparison methods (``lessThan``...) and
``decimal``/``datetime`` literals. Anything else (``like``, entity hierarchy
``in``, arithmetic, ``if``, tags) raises ``ConditionNotSupported``: the
operation fails closed instead of guessing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from resource_engine.authz.conditions import conjunction, disjunction, negation
from resource_engine.errors import ConditionNotSupported

_COMPARE = {"==": "eq", "<": "lt", "<=": "le", ">": "gt", ">=": "ge", "!=": "ne"}
_METHODS = {
    "lessThan": "lt",
    "lessThanOrEqual": "le",
    "greaterThan": "gt",
    "greaterThanOrEqual": "ge",
}


class _Attr(str):
    """An attribute path resolved from the residual (``resource.amount``)."""


def residual_condition(
    residuals: Mapping[str, Any], *, resource_type: str | None, context: Mapping[str, Any]
) -> Any:
    """``or(permits) and not or(forbids)`` from ``is_authorized_partial``
    residuals. ``resource_type`` (Cedar type) settles ``resource is T``;
    ``context`` supplies known context values when the context was left unknown
    (only ``context.input.*`` stays a condition)."""
    translator = _Translator(resource_type, context)
    permits, forbids = [], []
    for policy_id, policy in residuals.items():
        try:
            condition = translator.policy(policy)
        except ConditionNotSupported as exc:
            raise ConditionNotSupported(
                f"Cedar policy {policy_id!r} cannot become a row filter: {exc.message}",
                hint="Use the supported operators (docs/authorizers.md#cedar) in conditions "
                "on the record, or grant the action without them.",
            ) from None
        (permits if policy.get("effect") == "permit" else forbids).append(condition)
    return conjunction(disjunction(*permits), negation(disjunction(*forbids)))


class _Translator:
    def __init__(self, resource_type: str | None, context: Mapping[str, Any]) -> None:
        self.resource_type = resource_type
        self.context = context

    def policy(self, policy: Mapping[str, Any]) -> Any:
        for scope in ("principal", "action", "resource"):
            if (policy.get(scope) or {}).get("op", "All") != "All":
                raise ConditionNotSupported(f"unresolved {scope} scope")
        parts = []
        for clause in policy.get("conditions") or ():
            body = self.boolean(clause["body"])
            parts.append(body if clause.get("kind", "when") == "when" else negation(body))
        return conjunction(*parts)

    # ------------------------------------------------------------- booleans

    def boolean(self, node: Any) -> Any:
        value = self.expr(node)
        if isinstance(value, bool) or _is_condition(value):
            return value
        raise ConditionNotSupported(f"non-boolean expression {_short(node)}")

    def expr(self, node: Any) -> Any:
        if not isinstance(node, Mapping) or len(node) != 1:
            raise ConditionNotSupported(f"unexpected node {_short(node)}")
        ((op, args),) = node.items()
        if op == "Value":
            return _literal(args)
        if op == "Set":
            return [self.value(item) for item in args]
        if op == "unknown":
            return _Attr(args[0]["Value"])
        if op == ".":
            return self.attribute(self.expr(args["left"]), args["attr"])
        if op == "&&":
            return conjunction(self.boolean(args["left"]), self.boolean(args["right"]))
        if op == "||":
            return disjunction(self.boolean(args["left"]), self.boolean(args["right"]))
        if op == "!":
            return negation(self.boolean(args["arg"]))
        if op in _COMPARE:
            return self.compare(_COMPARE[op], args["left"], args["right"])
        if op in _METHODS:
            return self.compare(_METHODS[op], args[0], args[1])
        if op in ("decimal", "datetime"):
            literal = self.value(args[0])
            return Decimal(literal) if op == "decimal" else literal
        if op == "has":
            target = self.expr(args["left"])
            if isinstance(target, _Attr):
                return {"exists": {"attr": f"{target}.{args['attr']}"}}
            raise ConditionNotSupported("`has` on a known value")
        if op == "contains":
            return self.member(args["left"], args["right"])
        if op == "in":
            right = self.expr(args["right"])
            if not isinstance(right, list):
                raise ConditionNotSupported("`in` on entities (hierarchy)")
            return self.membership(self.expr(args["left"]), right)
        if op == "is":
            target = self.expr(args["left"])
            if target == "resource" and self.resource_type is not None:
                return args["entity_type"] == self.resource_type
            raise ConditionNotSupported("`is` on an unknown entity")
        raise ConditionNotSupported(f"operator {op!r}")

    def value(self, node: Any) -> Any:
        value = self.expr(node)
        if _is_condition(value) and not isinstance(value, _Attr):
            raise ConditionNotSupported(f"condition used as a value {_short(node)}")
        return value

    def attribute(self, target: Any, name: str) -> Any:
        if isinstance(target, _Attr):
            path = f"{target}.{name}"
            if path.startswith("context.") and not path.startswith("context.input"):
                return _known(self.context, path.removeprefix("context."))
            if path == "context.input":
                return _Attr("input")
            if path.startswith("context.input."):
                return _Attr(path.removeprefix("context."))
            return _Attr(path)
        if isinstance(target, Mapping):
            return target.get(name)
        raise ConditionNotSupported(f"attribute {name!r} of a literal")

    def operand(self, value: Any) -> Any:
        if isinstance(value, _Attr):
            if value in ("resource", "input"):
                raise ConditionNotSupported(f"comparison with the whole {value}")
            return {"attr": str(value)}
        if isinstance(value, dict):
            raise ConditionNotSupported("comparison with a record")
        return value

    def compare(self, op: str, left: Any, right: Any) -> Any:
        a, b = self.value(left), self.value(right)
        if not isinstance(a, _Attr) and not isinstance(b, _Attr):
            from resource_engine.authz.conditions import Env, evaluate

            return evaluate({op: [a, b]}, Env()) is True
        return {op: [self.operand(a), self.operand(b)]}

    def member(self, collection: Any, element: Any) -> Any:
        c, e = self.value(collection), self.value(element)
        if isinstance(c, list):
            return self.membership(e, c)
        if isinstance(c, _Attr):
            return {"contains": [self.operand(c), self.operand(e)]}
        raise ConditionNotSupported("`contains` on a non-set value")

    def membership(self, element: Any, items: list[Any]) -> Any:
        if not isinstance(element, _Attr):
            return element in items
        if any(isinstance(i, _Attr) for i in items):
            raise ConditionNotSupported("set of attributes")
        return {"in": [self.operand(element), items]}


def _known(context: Mapping[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _literal(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "__entity" in value:
            raise ConditionNotSupported("entity literal (compare ids: `principal.id`)")
        if "__extn" in value:
            ext = value["__extn"]
            return Decimal(ext["arg"]) if ext["fn"] == "decimal" else ext["arg"]
        return dict(value)
    return value


def _is_condition(value: Any) -> bool:
    return isinstance(value, Mapping) and len(value) == 1 and not isinstance(value, _Attr)


def _short(node: Any) -> str:
    text = str(node)
    return text if len(text) < 80 else text[:77] + "..."
