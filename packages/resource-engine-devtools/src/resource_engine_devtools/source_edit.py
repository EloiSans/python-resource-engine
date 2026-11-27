"""Small, comment-preserving edits of Python source for the scaffold commands:
add an import, append to a class attribute's list or dict, add a field to a
class, add an argument to a call.

Edits insert lines; they never rewrite what is there except a one-line
literal or import that has to grow (re-rendered on several lines). The result
is formatted the way ``ruff format`` leaves the generated modules.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable

INDENT = "    "
MAX_LINE = 100


class EditError(ValueError):
    """The source doesn't have the shape the edit expects."""


def _lines(source: str) -> list[str]:
    return source.splitlines(keepends=True)


def _join(lines: list[str]) -> str:
    text = "".join(lines)
    return text if text.endswith("\n") else text + "\n"


def _segment(source: str, node: ast.AST) -> str:
    text = ast.get_source_segment(source, node)
    if text is None:  # pragma: no cover - nodes from this source always have one
        raise EditError("Cannot read the source of a node")
    return text


# ------------------------------------------------------------------ imports


def add_import(source: str, module: str, name: str) -> str:
    """``from module import name``, merged into an existing import of ``module``."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == module and node.level == 0:
            names = [a.name if a.asname is None else f"{a.name} as {a.asname}" for a in node.names]
            if name in names:
                return source
            names = sorted({*names, name}, key=_import_order)
            lines = _lines(source)
            rendered = _render_import(module, names)
            lines[node.lineno - 1 : node.end_lineno] = [rendered]
            return _join(lines)
    imports = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    lines = _lines(source)
    line = f"from {module} import {name}\n"
    if not imports:
        position = _after_docstring(tree)
        lines[position:position] = [line, "\n"] if position == 0 else ["\n", line]
        return _join(lines)
    # sorted among the imports of the same package, else of the same section
    # (__future__, standard library, others), else as a section of its own
    top = module.split(".")[0]
    section = _section(module)
    same = [n for n in imports if _module(n).split(".")[0] == top]
    same = same or [n for n in imports if _section(_module(n)) == section]
    if same:
        later = [n for n in same if _module(n) > module]
        if later:
            anchor = min(later, key=lambda n: n.lineno)
            lines[anchor.lineno - 1 : anchor.lineno - 1] = [line]
        else:
            anchor = max(same, key=lambda n: n.end_lineno or 0)
            lines[anchor.end_lineno : anchor.end_lineno] = [line]  # type: ignore[misc]
        return _join(lines)
    earlier = [n for n in imports if _section(_module(n)) < section]
    if earlier:
        anchor = max(earlier, key=lambda n: n.end_lineno or 0)
        lines[anchor.end_lineno : anchor.end_lineno] = ["\n", line]  # type: ignore[misc]
    else:
        anchor = min(imports, key=lambda n: n.lineno)
        lines[anchor.lineno - 1 : anchor.lineno - 1] = [line, "\n"]
    return _join(lines)


def _module(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.ImportFrom):
        return node.module or ""
    return node.names[0].name


def _section(module: str) -> int:
    top = module.split(".")[0]
    if top == "__future__":
        return 0
    return 1 if top in sys.stdlib_module_names else 2


def _import_order(name: str) -> tuple[int, str]:
    # ruff (isort) order: CONSTANTS, Classes, functions
    if name.isupper():
        return (0, name)
    if name[:1].isupper():
        return (1, name)
    return (2, name)


def _render_import(module: str, names: list[str]) -> str:
    line = f"from {module} import {', '.join(names)}\n"
    if len(line) - 1 <= MAX_LINE:
        return line
    return f"from {module} import (\n" + "".join(f"{INDENT}{n},\n" for n in names) + ")\n"


def _after_docstring(tree: ast.Module) -> int:
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.end_lineno or 0
    return 0


# ------------------------------------------------------------------ classes


def find_class(tree: ast.Module, predicate: Callable[[ast.ClassDef], bool]) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and predicate(node):
            return node
    return None


def _class(source: str, name: str) -> tuple[ast.Module, ast.ClassDef]:
    tree = ast.parse(source)
    cls = find_class(tree, lambda c: c.name == name)
    if cls is None:
        raise EditError(f"No class {name}")
    return tree, cls


def class_attribute(cls: ast.ClassDef, attr: str) -> ast.Assign | ast.AnnAssign | None:
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == attr for t in node.targets
        ):
            return node
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == attr
        ):
            return node
    return None


def attribute_value(source: str, class_name: str, attr: str) -> ast.expr | None:
    _, cls = _class(source, class_name)
    node = class_attribute(cls, attr)
    return None if node is None else node.value


def _last_attribute(cls: ast.ClassDef) -> ast.stmt:
    attrs = [n for n in cls.body if isinstance(n, ast.Assign | ast.AnnAssign)]
    return attrs[-1] if attrs else cls.body[-1]


def _insert_after(source: str, node: ast.stmt, text: str) -> str:
    lines = _lines(source)
    lines[node.end_lineno : node.end_lineno] = [text]  # type: ignore[misc]
    return _join(lines)


def add_class_attribute(source: str, class_name: str, attr: str, value: str) -> str:
    """``attr = value`` after the class's last attribute."""
    _, cls = _class(source, class_name)
    if class_attribute(cls, attr) is not None:
        raise EditError(f"{class_name}.{attr} already exists")
    return _insert_after(source, _last_attribute(cls), _indent(f"{attr} = {value}", 1) + "\n")


def _indent(text: str, level: int) -> str:
    pad = INDENT * level
    return "\n".join(pad + line if line else line for line in text.split("\n"))


def _container_insert(source: str, node: ast.List | ast.Dict, item: str, level: int) -> str:
    """Add ``item`` (an element, or ``key: value``) at the end of a literal whose
    statement starts at indentation ``level``."""
    lines = _lines(source)
    elements = node.elts if isinstance(node, ast.List) else node.values
    multiline = node.end_lineno != node.lineno
    if multiline:
        closing = node.end_lineno - 1  # type: ignore[operator]
        if lines[closing].strip()[:1] not in ("]", "}"):
            raise EditError("Unexpected layout: the closing bracket shares a line")
        if elements:
            last = elements[-1]
            row = last.end_lineno - 1  # type: ignore[operator]
            text = lines[row]
            col = last.end_col_offset
            rest = text[col:]
            if not rest.lstrip().startswith(","):
                lines[row] = text[:col] + "," + rest
        lines[closing:closing] = [_indent(item, level + 1) + ",\n"]
        return _join(lines)
    # one line: re-render on several lines
    if isinstance(node, ast.List):
        items = [_segment(source, e) for e in node.elts]
        opening, closing_char = "[", "]"
    else:
        items = [
            f"{_segment(source, k)}: {_segment(source, v)}"
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None
        ]
        opening, closing_char = "{", "}"
    items.append(item)
    row = node.lineno - 1
    text = lines[row]
    before, after = text[: node.col_offset], text[node.end_col_offset :]
    body = "".join(_indent(i, level + 1) + ",\n" for i in items)
    lines[row] = before + opening + "\n" + body + INDENT * level + closing_char + after
    return _join(lines)


def _literal(opening: str, item: str, closing: str, attr: str) -> str:
    """A new one-item literal: on one line when it fits, else one item per line."""
    one_line = f"{opening}{item}{closing}"
    if "\n" not in item and len(INDENT + attr + " = " + one_line) <= MAX_LINE:
        return one_line
    return f"{opening}\n{_indent(item, 1)},\n{closing}"


def append_to_list(source: str, class_name: str, attr: str, item: str) -> str:
    """Add ``item`` to the list ``attr`` of the class (created when missing)."""
    _, cls = _class(source, class_name)
    node = class_attribute(cls, attr)
    if node is None:
        return add_class_attribute(source, class_name, attr, _literal("[", item, "]", attr))
    if not isinstance(node.value, ast.List):
        raise EditError(f"{class_name}.{attr} is not a list literal")
    return _container_insert(source, node.value, item, 1)


def add_to_dict(source: str, class_name: str, attr: str, key: str, value: str) -> str:
    """Add ``"key": value`` to the dict ``attr`` (created when missing)."""
    _, cls = _class(source, class_name)
    node = class_attribute(cls, attr)
    entry = f'"{key}": {value}'
    if node is None:
        return add_class_attribute(source, class_name, attr, _literal("{", entry, "}", attr))
    if not isinstance(node.value, ast.Dict):
        raise EditError(f"{class_name}.{attr} is not a dict literal")
    if key in dict_keys(node.value):
        raise EditError(f"{class_name}.{attr} already has {key!r}")
    return _container_insert(source, node.value, entry, 1)


def add_to_dict_list(source: str, class_name: str, attr: str, key: str, item: str) -> str:
    """Add ``item`` to the list under ``"key"`` in the dict ``attr`` (hooks)."""
    _, cls = _class(source, class_name)
    node = class_attribute(cls, attr)
    if node is None or not isinstance(node.value, ast.Dict) or key not in dict_keys(node.value):
        return add_to_dict(source, class_name, attr, key, f"[{item}]")
    value = node.value.values[dict_keys(node.value).index(key)]
    if isinstance(value, ast.List):
        if item in [_segment(source, e) for e in value.elts]:
            raise EditError(f"{key} already runs {item}")
        lines = _lines(source)
        if value.end_lineno == value.lineno:
            row = value.lineno - 1
            text = lines[row]
            items = [_segment(source, e) for e in value.elts] + [item]
            grown = text[: value.col_offset] + "[" + ", ".join(items) + "]"
            grown += text[value.end_col_offset :]
            if len(grown.rstrip("\n")) <= MAX_LINE:
                lines[row] = grown
                return _join(lines)
        return _container_insert(source, value, item, 2)
    # a single hook: make it a list
    lines = _lines(source)
    row = value.lineno - 1
    if value.end_lineno != value.lineno:
        raise EditError(f"Unexpected layout for {key}")
    text = lines[row]
    before, after = text[: value.col_offset], text[value.end_col_offset :]
    lines[row] = f"{before}[{_segment(source, value)}, {item}]{after}"
    return _join(lines)


def dict_keys(node: ast.Dict) -> list[str | None]:
    return [k.value if isinstance(k, ast.Constant) else None for k in node.keys]


def list_call_names(node: ast.expr | None, callee: str) -> list[str]:
    """First string argument of each ``callee(...)`` in a list literal."""
    if not isinstance(node, ast.List):
        return []
    names = []
    for element in node.elts:
        if (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Name)
            and element.func.id == callee
            and element.args
            and isinstance(element.args[0], ast.Constant)
        ):
            names.append(str(element.args[0].value))
    return names


# ------------------------------------------------------------------ fields


def class_fields(source: str, class_name: str) -> list[str]:
    _, cls = _class(source, class_name)
    return [
        n.target.id
        for n in cls.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    ]


def add_field(
    source: str, class_name: str, line: str, *, required: bool, before: tuple[str, ...] = ()
) -> str:
    """Add an annotated field. A required one goes before the first field with
    a default (dataclass order); an optional one before the first of
    ``before`` (engine fields such as ``status``, ``id``), else last."""
    _, cls = _class(source, class_name)
    fields = [
        n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    ]
    anchor = None
    if required:
        anchor = next((f for f in fields if f.value is not None), None)
    else:
        anchor = next((f for f in fields if f.target.id in before), None)  # type: ignore[union-attr]
    lines = _lines(source)
    text = _indent(line, 1) + "\n"
    if anchor is not None:
        lines[anchor.lineno - 1 : anchor.lineno - 1] = [text]
        return _join(lines)
    last = fields[-1] if fields else _last_attribute(cls)
    lines[last.end_lineno : last.end_lineno] = [text]  # type: ignore[misc]
    return _join(lines)


def add_call_argument(source: str, variable: str, argument: str) -> str:
    """Add an argument at the end of the call assigned to a module variable
    (``orders_table = Table(...)``)."""
    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            call = node.value
            lines = _lines(source)
            closing = call.end_lineno - 1  # type: ignore[operator]
            if call.end_lineno == call.lineno or lines[closing].strip()[:1] != ")":
                raise EditError(f"{variable} must be a call laid out on several lines")
            arguments = [*call.args, *(k.value for k in call.keywords)]
            if arguments:
                last = arguments[-1]
                row = last.end_lineno - 1  # type: ignore[operator]
                text = lines[row]
                col = last.end_col_offset
                if not text[col:].lstrip().startswith(","):
                    lines[row] = text[:col] + "," + text[col:]
            lines[closing:closing] = [_indent(argument, 1) + ",\n"]
            return _join(lines)
    raise EditError(f"No call assigned to {variable}")


# ------------------------------------------------------------- module level


def module_variable(source: str, name: str) -> ast.Assign | ast.AnnAssign | None:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node
    return None


def append_to_module_list(source: str, name: str, item: str) -> str:
    node = module_variable(source, name)
    if node is None or not isinstance(node.value, ast.List):
        raise EditError(f"{name} is not a list literal")
    return _container_insert(source, node.value, item, 0)


def defines(source: str, name: str) -> bool:
    """Whether the module defines a top-level class or function ``name``."""
    tree = ast.parse(source)
    return any(
        isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        for n in tree.body
    )
