"""The condition language of policies: JSON, safe to store and to edit from a UI.

A condition is a JSON value:

- an operator object with one key: ``{"eq": [a, b]}``, ``{"and": [c1, c2]}``,
  ``{"not": c}``, ``{"exists": a}``;
- operands are literals (string, number, boolean, null, list) or attribute
  references ``{"attr": "resource.amount"}``.

Operators: ``eq ne lt le gt ge`` (two operands), ``in nin`` (value, list),
``contains`` (list, value), ``exists`` (one operand), ``and or`` (list of
conditions), ``not`` (one condition).

Attribute namespaces: ``subject`` (the caller), ``resource`` (the record),
``input`` (validated input of the operation) and ``context`` (operation data).

Evaluation is three-valued: an attribute of a namespace that is not known yet
(the record before it is loaded) makes the result ``UNKNOWN``. ``partial`` keeps
the unknown parts as a residual condition, which repositories apply as a query
filter (row-level visibility).
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

COMPARISONS = ("eq", "ne", "lt", "le", "gt", "ge")
MEMBERSHIP = ("in", "nin", "contains")
LOGICAL = ("and", "or", "not")
OPERATORS = (*COMPARISONS, *MEMBERSHIP, "exists", *LOGICAL)
NAMESPACES = ("subject", "resource", "input", "context")


class _Unknown(enum.Enum):
    UNKNOWN = "unknown"

    def __bool__(self) -> bool:  # pragma: no cover - guard against accidental use
        raise TypeError("UNKNOWN has no truth value; compare with `is UNKNOWN`")


UNKNOWN = _Unknown.UNKNOWN
type Tristate = bool | _Unknown
type Condition = Any  # JSON value


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


# ---------------------------------------------------------------- structure


def is_attr(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {"attr"} and isinstance(value["attr"], str)


def _op(node: Any) -> tuple[str, Any]:
    if not isinstance(node, Mapping) or len(node) != 1:
        raise ValueError("a condition is an object with exactly one operator key")
    ((op, args),) = node.items()
    if op not in OPERATORS:
        raise ValueError(f"unknown operator {op!r}")
    return op, args


def attributes(node: Condition) -> set[str]:
    """Attribute paths referenced by a condition."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if is_attr(value):
            found.add(value["attr"])
        elif isinstance(value, Mapping):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(node)
    return found


# --------------------------------------------------------------- validation

#: Attribute types published by the catalogue.
TYPES = ("string", "number", "boolean", "datetime", "date", "list", "object", "any")
_ORDERED = {"number", "datetime", "date", "string", "any"}


def _literal_type(value: Any) -> str:
    if value is None:
        return "any"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float | Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "object"
    return "any"


def _compatible(a: str, b: str) -> bool:
    if "any" in (a, b) or a == b:
        return True
    # dates are written as ISO strings in JSON
    return {a, b} <= {"string", "date", "datetime"}


def validate(
    node: Condition,
    types: Mapping[str, str] | Callable[[str], str | None],
    *,
    path: str = "$",
) -> list[Problem]:
    """Structural and type problems of a condition (empty list when valid).

    ``types`` maps attribute paths to catalogue types, or is a function returning
    the type or ``None`` for an unknown attribute.
    """
    lookup = types if callable(types) else types.get
    problems: list[Problem] = []

    def operand_type(value: Any, at: str) -> str | None:
        if is_attr(value):
            name = value["attr"]
            if name.split(".", 1)[0] not in NAMESPACES:
                problems.append(Problem(at, f"unknown namespace in {name!r}"))
                return None
            kind = lookup(name)
            if kind is None:
                problems.append(Problem(at, f"unknown attribute {name!r}"))
            return kind
        if isinstance(value, Mapping):
            problems.append(Problem(at, "operands are literals or {'attr': ...} references"))
            return None
        return _literal_type(value)

    def check(value: Any, at: str) -> None:
        try:
            op, args = _op(value)
        except ValueError as exc:
            problems.append(Problem(at, str(exc)))
            return
        here = f"{at}.{op}"
        if op in ("and", "or"):
            if not isinstance(args, list) or not args:
                problems.append(Problem(here, f"'{op}' takes a non-empty list of conditions"))
                return
            for i, child in enumerate(args):
                check(child, f"{here}[{i}]")
        elif op == "not":
            check(args, here)
        elif op == "exists":
            if not is_attr(args):
                problems.append(Problem(here, "'exists' takes an attribute reference"))
            else:
                operand_type(args, here)
        else:
            if not isinstance(args, list) or len(args) != 2:
                problems.append(Problem(here, f"'{op}' takes two operands"))
                return
            left = operand_type(args[0], f"{here}[0]")
            right = operand_type(args[1], f"{here}[1]")
            if left is None or right is None:
                return
            if op in ("in", "nin") and right not in ("list", "any"):
                problems.append(Problem(f"{here}[1]", f"'{op}' needs a list on the right"))
            elif op == "contains" and left not in ("list", "any"):
                problems.append(Problem(f"{here}[0]", "'contains' needs a list on the left"))
            elif op in COMPARISONS and not _compatible(left, right):
                problems.append(Problem(here, f"cannot compare {left} with {right}"))
            elif op in ("lt", "le", "gt", "ge") and not {left, right} <= _ORDERED:
                problems.append(Problem(here, f"'{op}' needs ordered values, got {left}"))

    check(node, path)
    return problems


# --------------------------------------------------------------- evaluation


class Env:
    """Attribute values for evaluation. Namespaces missing from ``known`` are
    UNKNOWN; missing attributes of a known namespace are ``None``."""

    def __init__(self, **namespaces: Any) -> None:
        self.namespaces = namespaces

    def resolve(self, name: str) -> Any:
        namespace, _, rest = name.partition(".")
        if namespace not in self.namespaces:
            return UNKNOWN
        value: Any = self.namespaces[namespace]
        for part in rest.split(".") if rest else ():
            if value is None:
                return None
            value = value.get(part) if isinstance(value, Mapping) else getattr(value, part, None)
        return value


def _normalise(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, frozenset | set | tuple):
        return list(value)
    return value


def _coerce_pair(a: Any, b: Any) -> tuple[Any, Any]:
    a, b = _normalise(a), _normalise(b)
    if isinstance(a, datetime) and isinstance(b, str):
        return a, datetime.fromisoformat(b)
    if isinstance(b, datetime) and isinstance(a, str):
        return datetime.fromisoformat(a), b
    if isinstance(a, date) and isinstance(b, str):
        return a, date.fromisoformat(b)
    if isinstance(b, date) and isinstance(a, str):
        return date.fromisoformat(a), b
    numeric = (int, float, Decimal)
    if (
        isinstance(a, numeric)
        and isinstance(b, numeric)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
        and (isinstance(a, Decimal) or isinstance(b, Decimal))
    ):
        try:
            return Decimal(str(a)), Decimal(str(b))
        except InvalidOperation:  # pragma: no cover
            return a, b
    return a, b


def _compare(op: str, a: Any, b: Any) -> bool:
    try:
        a, b = _coerce_pair(a, b)
    except ValueError:
        return False
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if a is None or b is None:
        return False
    try:
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    except TypeError:
        return False


def _member(value: Any, collection: Any) -> bool:
    collection = _normalise(collection)
    if not isinstance(collection, list):
        return False
    return any(_compare("eq", value, item) for item in collection)


def _and(values: Iterable[Tristate]) -> Tristate:
    unknown = False
    for v in values:
        if v is False:
            return False
        if v is UNKNOWN:
            unknown = True
    return UNKNOWN if unknown else True


def _or(values: Iterable[Tristate]) -> Tristate:
    unknown = False
    for v in values:
        if v is True:
            return True
        if v is UNKNOWN:
            unknown = True
    return UNKNOWN if unknown else False


def evaluate(node: Condition, env: Env) -> Tristate:
    """Evaluate ``node``; ``None`` (no condition) is ``True``."""
    if node is None:
        return True
    op, args = _op(node)
    if op == "and":
        return _and(evaluate(c, env) for c in args)
    if op == "or":
        return _or(evaluate(c, env) for c in args)
    if op == "not":
        inner = evaluate(args, env)
        return inner if inner is UNKNOWN else not inner
    if op == "exists":
        value = env.resolve(args["attr"])
        return value if value is UNKNOWN else value is not None

    def value_of(operand: Any) -> Any:
        return env.resolve(operand["attr"]) if is_attr(operand) else operand

    a, b = value_of(args[0]), value_of(args[1])
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    if op in COMPARISONS:
        return _compare(op, a, b)
    if op == "in":
        return _member(a, b)
    if op == "nin":
        return not _member(a, b)
    return _member(b, a)  # contains


# ----------------------------------------------------------- partial evaluation


def partial(node: Condition, env: Env) -> Condition | bool:
    """Substitute known attributes and simplify.

    Returns ``True``/``False`` when decided, otherwise a residual condition that
    only references attributes of unknown namespaces (e.g. ``resource.*``).
    """
    if node is None:
        return True
    op, args = _op(node)
    if op in ("and", "or"):
        parts = [partial(c, env) for c in args]
        absorbing, neutral = (False, True) if op == "and" else (True, False)
        if any(p is absorbing for p in parts):
            return absorbing
        rest = [p for p in parts if p is not neutral]
        if not rest:
            return neutral
        return rest[0] if len(rest) == 1 else {op: rest}
    if op == "not":
        inner = partial(args, env)
        return (not inner) if isinstance(inner, bool) else {"not": inner}
    result = evaluate(node, env)
    if result is not UNKNOWN:
        return bool(result)

    def substitute(operand: Any) -> Any:
        if is_attr(operand):
            value = env.resolve(operand["attr"])
            return operand if value is UNKNOWN else _normalise(value)
        return operand

    if op == "exists":
        return node
    return {op: [substitute(args[0]), substitute(args[1])]}


def conjunction(*parts: Condition | bool | None) -> Condition | bool:
    """AND of conditions, simplified (``None`` and ``True`` are neutral)."""
    rest = []
    for p in parts:
        if p is None or p is True:
            continue
        if p is False:
            return False
        rest.append(p)
    if not rest:
        return True
    return rest[0] if len(rest) == 1 else {"and": rest}


def disjunction(*parts: Condition | bool) -> Condition | bool:
    rest = []
    for p in parts:
        if p is True:
            return True
        if p is False or p is None:
            continue
        rest.append(p)
    if not rest:
        return False
    return rest[0] if len(rest) == 1 else {"or": rest}


def negation(part: Condition | bool) -> Condition | bool:
    return (not part) if isinstance(part, bool) else {"not": part}


def matches(condition: Condition | bool | None, entity: Any) -> bool:
    """Apply a residual (``resource.*``) condition to an entity: the in-memory
    query filter."""
    if condition is None or condition is True:
        return True
    if condition is False:
        return False
    return evaluate(condition, Env(resource=entity)) is True
