"""``resource-engine scaffold hook|event|slot|reference|policy``: the remaining
functional elements of an existing module, written and declared on the
resource in one step.

Each command writes its code (a hook function, an event class, a seed policy)
and edits the resource class so the element is registered: the module keeps
importing, registering and passing ``check``. Edits add lines and keep
comments (``source_edit``); names that already exist are refused.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path

from resource_engine_devtools import source_edit as edit
from resource_engine_devtools.scaffold import (
    TYPES,
    Field,
    Result,
    ScaffoldError,
    _existing,
    _identifier,
    _render,
    pascal,
    singular,
)

HOOK_POINTS = (
    "before_validate",
    "after_validate",
    "before_create",
    "after_create",
    "before_update",
    "after_update",
    "before_delete",
    "after_delete",
    "before_transition",
    "after_transition",
    "before_action",
    "after_action",
    "before_attach",
    "after_attach",
    "before_detach",
    "after_detach",
    "after_commit",
)
QUALIFIABLE = {
    p for p in HOOK_POINTS if p.endswith(("_transition", "_action", "_attach", "_detach"))
}
ON_DELETE = ("restrict", "set_null", "cascade")


# ----------------------------------------------------------- the resource


class _ResourceFile:
    """The module file declaring a resource, edited in memory and written once."""

    def __init__(self, base: Path, package: str, resource: str) -> None:
        self.package = package
        self.resource = resource
        found = []
        for path in sorted((base / "application").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for cls in ast.parse(source).body:
                if isinstance(cls, ast.ClassDef) and _declares(cls, resource):
                    found.append((path, cls.name, source))
        if not found:
            raise ScaffoldError(
                f"No Resource for {resource!r} in {base / 'application'} "
                "(a class with name = ... or the default name)"
            )
        self.path, self.class_name, self.source = found[0]
        self.module = f"{package}.application.{self.path.stem}"

    def attribute(self, attr: str) -> ast.expr | None:
        return edit.attribute_value(self.source, self.class_name, attr)

    def apply(self, change) -> None:  # type: ignore[no-untyped-def]
        try:
            self.source = change(self.source)
        except edit.EditError as exc:
            raise ScaffoldError(f"{self.path}: {exc}") from None

    def write(self, result: Result, *, dry_run: bool) -> None:
        _save(self.path, self.source, result, dry_run=dry_run)


def _declares(cls: ast.ClassDef, resource: str) -> bool:
    if not any(_base_name(b).endswith("Resource") for b in cls.bases):
        return False
    attr = edit.class_attribute(cls, "name")
    if attr is not None and isinstance(attr.value, ast.Constant):
        return attr.value.value == resource
    base = cls.name.removesuffix("Resource") or cls.name
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
    return (snake if snake.endswith("s") else f"{snake}s") == resource


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _save(path: Path, content: str, result: Result, *, dry_run: bool) -> None:
    result.written.append(str(path))
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _read(path: Path, default: str) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else default


def _string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ------------------------------------------------------------------- hook


def hook(
    root: Path,
    package: str,
    resource: str,
    function: str,
    points: list[str],
    *,
    effect: bool = False,
    dry_run: bool = False,
) -> Result:
    """A hook function in ``application/hooks.py``, run at ``points``."""
    _identifier(function, "hook function")
    if not points:
        raise ScaffoldError("Name at least one point with --on (before_create, after_commit...)")
    for point in points:
        base_point, _, qualifier = point.partition(":")
        if base_point not in HOOK_POINTS:
            raise ScaffoldError(
                f"Unknown hook point {point!r}; use one of: {', '.join(HOOK_POINTS)}"
            )
        if qualifier and base_point not in QUALIFIABLE:
            raise ScaffoldError(f"{base_point} takes no qualifier")
        if effect and base_point != "after_commit":
            raise ScaffoldError(
                "External effects run after the commit: use --on after_commit with --effect "
                "(an effect inside the transaction is RE-C002)"
            )
    base = _existing(root, package)
    target = _ResourceFile(base, package, resource)
    path = base / "application/hooks.py"
    source = _read(
        path, "from __future__ import annotations\n\nfrom resource_engine import OperationContext\n"
    )
    if edit.defines(source, function):
        raise ScaffoldError(f"{path} already defines {function}")
    source = edit.add_import(source, "resource_engine", "OperationContext")
    asynchronous = effect or any(p.startswith("after_commit") for p in points)
    if effect:
        source = edit.add_import(source, "resource_engine", "external_effect")
    when = ", ".join(points)
    if effect:
        docstring = (
            f'"""TODO: say what happens outside the system after {when}.\n\n'
            '    Effects:\n        TODO: the external system and what it receives.\n    """'
        )
    else:
        docstring = f'"""TODO: say what this checks or changes ({when})."""'
    body = _render(
        """
$decorator${prefix}def $function(ctx: OperationContext) -> None:
    $docstring
""",
        decorator="@external_effect\n" if effect else "",
        prefix="async " if asynchronous else "",
        function=function,
        docstring=docstring,
    )
    source = source.rstrip("\n") + "\n\n\n" + body
    result = Result()
    _save(path, source, result, dry_run=dry_run)
    target.apply(lambda s: edit.add_import(s, f"{package}.application", "hooks"))
    for point in points:
        target.apply(
            lambda s, p=point: edit.add_to_dict_list(
                s, target.class_name, "hooks", p, f"hooks.{function}"
            )
        )
    target.write(result, dry_run=dry_run)
    result.next_steps = [
        f"Implement {function} in {path} and replace its TODO docstring",
        "Hooks before the commit run inside the transaction: raise a DomainError to refuse"
        + (
            "; external calls belong in after_commit hooks marked @external_effect"
            if not effect
            else ""
        ),
    ]
    return result


# ------------------------------------------------------------------ event


def event(
    root: Path,
    package: str,
    resource: str,
    class_name: str,
    fields: list[Field],
    *,
    dry_run: bool = False,
) -> Result:
    """A domain event in ``domain/events.py``, declared in ``Resource.events``."""
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", class_name):
        raise ScaffoldError(f"Invalid event class name {class_name!r}: use PascalCase")
    base = _existing(root, package)
    target = _ResourceFile(base, package, resource)
    path = base / "domain/events.py"
    source = _read(
        path,
        "from __future__ import annotations\n\nfrom dataclasses import dataclass\n\n"
        "from resource_engine.domain import DomainEvent\n",
    )
    if edit.defines(source, class_name):
        raise ScaffoldError(f"{path} already defines {class_name}")
    source = edit.add_import(source, "dataclasses", "dataclass")
    source = edit.add_import(source, "resource_engine.domain", "DomainEvent")
    for module, name in sorted({_type_import(f) for f in fields if _type_import(f)}):  # type: ignore[misc]
        source = edit.add_import(source, module, name)
    entity_id = f"{singular(resource)}_id"
    lines = [f"    {entity_id}: int"] + [
        f"    {f.name}: {f.annotation}" + (" = None" if f.optional else "")
        for f in fields
        if f.name != entity_id
    ]
    body = _render(
        '''
@dataclass(frozen=True, kw_only=True)
class $cls(DomainEvent):
    """TODO: say what happened, in the past tense."""

$fields
''',
        cls=class_name,
        fields="\n".join(lines),
    )
    source = source.rstrip("\n") + "\n\n\n" + body
    result = Result()
    _save(path, source, result, dry_run=dry_run)
    target.apply(lambda s: edit.add_import(s, f"{package}.domain.events", class_name))
    target.apply(lambda s: edit.append_to_list(s, target.class_name, "events", class_name))
    target.write(result, dry_run=dry_run)
    result.next_steps = [
        f"Record it where it happens (an action or a hook): ctx.record_event({class_name}("
        f"{entity_id}=ctx.entity.id, ...))",
        "It is published after the commit with the engine's events and listed in the "
        "AsyncAPI document (resource-engine docs asyncapi)",
        "Replace the TODO docstring: the generated docs and AsyncAPI show it",
    ]
    return result


def _type_import(f: Field) -> tuple[str, str] | None:
    statement = TYPES[f.type][2]
    if statement is None:
        return None
    module, _, name = statement.removeprefix("from ").partition(" import ")
    return module, name


# ------------------------------------------------------------------- slot


def slot(
    root: Path,
    package: str,
    resource: str,
    name: str,
    *,
    content_types: list[str] | None = None,
    max_mb: int | None = None,
    multiple: bool = False,
    upload: str | None = None,
    required_for: list[str] | None = None,
    processing: bool = False,
    dry_run: bool = False,
) -> Result:
    """A ``DocumentSlot`` in ``Resource.documents``."""
    _identifier(name, "slot")
    if upload not in (None, "api", "presigned", "any"):
        raise ScaffoldError("--upload must be api, presigned or any")
    base = _existing(root, package)
    target = _ResourceFile(base, package, resource)
    if name in edit.list_call_names(target.attribute("documents"), "DocumentSlot"):
        raise ScaffoldError(f"{resource} already has a {name!r} slot")
    arguments = [_string(name)]
    if content_types:
        arguments.append("content_types=[" + ", ".join(_string(t) for t in content_types) + "]")
    if max_mb:
        arguments.append(f"max_bytes={max_mb} * 1024 * 1024")
    if multiple:
        arguments.append("multiple=True")
    if upload:
        arguments.append(f"upload={_string(upload)}")
    if required_for:
        arguments.append("required_for=[" + ", ".join(_string(t) for t in required_for) + "]")
    if processing:
        arguments.append("processing=True")
    arguments.append('description="TODO: say what goes in this slot."')
    one_line = "DocumentSlot(" + ", ".join(arguments) + ")"
    call = (
        one_line
        if len(one_line) <= 72
        else "DocumentSlot(\n" + "".join(f"    {a},\n" for a in arguments) + ")"
    )
    target.apply(lambda s: edit.add_import(s, "resource_engine", "DocumentSlot"))
    target.apply(lambda s: edit.append_to_list(s, target.class_name, "documents", call))
    result = Result()
    target.write(result, dry_run=dry_run)
    result.next_steps = [
        "Give the manager a blob store: ResourceManager(..., blob_store=S3BlobStore(...)) "
        "(InMemoryBlobStore in tests, the module's own included), and register the "
        "documents resource (registry.register_documents())",
        f"Grant it: {resource}:attach:{name}, {resource}:detach:{name}, "
        f"{resource}:download:{name} in a seed policy or a system role",
        f"HTTP: POST /{resource}/{{id}}/documents/{name}, GET /{resource}/{{id}}/documents",
        "Replace the TODO description: the generated docs show it",
    ]
    return result


# -------------------------------------------------------------- reference


def reference(
    root: Path,
    package: str,
    resource: str,
    field_name: str,
    to: str,
    *,
    relation: str | None = None,
    on_delete: str = "restrict",
    required: bool = False,
    related_name: str | None = None,
    dry_run: bool = False,
) -> Result:
    """A ``Reference`` in ``Resource.references``; the field is added to the
    entity, the schemas and the table when the module doesn't have it."""
    _identifier(field_name, "field")
    _identifier(to, "resource")
    if relation:
        _identifier(relation, "relation")
    if related_name:
        _identifier(related_name, "relation")
    if on_delete not in ON_DELETE:
        raise ScaffoldError(f"--on-delete must be one of: {', '.join(ON_DELETE)}")
    if on_delete == "set_null" and required:
        raise ScaffoldError("set_null needs an optional field (drop --required)")
    base = _existing(root, package)
    target = _ResourceFile(base, package, resource)
    current = target.attribute("references")
    if isinstance(current, ast.Dict) and field_name in edit.dict_keys(current):
        raise ScaffoldError(f"{resource} already references through {field_name!r}")
    result = Result()
    renamed = None
    if relation is None and field_name.removesuffix("_id") in _entity_fields(base, target):
        relation = renamed = field_name.removesuffix("_id") + "_record"
    entity = _added_field(base, package, target, field_name, required, result, dry_run=dry_run)
    arguments = [_string(to)]
    if relation:
        arguments.append(f"name={_string(relation)}")
    if on_delete != "restrict":
        arguments.append(f"on_delete={_string(on_delete)}")
    if related_name:
        arguments.append(f"related_name={_string(related_name)}")
    label = field_name.removesuffix("_id").replace("_", " ")
    arguments.append(f'description="TODO: say what the {label} is to this record."')
    call = "Reference(\n" + "".join(f"    {a},\n" for a in arguments) + ")"
    target.apply(lambda s: edit.add_import(s, "resource_engine", "Reference"))
    target.apply(lambda s: edit.add_to_dict(s, target.class_name, "references", field_name, call))
    target.write(result, dry_run=dry_run)
    name = relation or field_name.removesuffix("_id")
    result.next_steps = [
        f"{to!r} must be registered in the same registry, the module's tests included "
        f"(tests/test_{resource}.py registers only {resource})",
        f"Writes check that {field_name} exists in {to} (RE-E108); deleting a referenced "
        f"{singular(to)} is "
        + {
            "restrict": "refused (RE-E109)",
            "set_null": "allowed and clears the field",
            "cascade": "allowed and deletes these records",
        }[on_delete],
        f"Reads: expand={name}, filters {name}.<field>=...; reverse: GET /{to}/{{id}}/"
        f"{related_name or resource}",
        (f"Add the {field_name} column to your migrations" if entity else "")
        + ("; update the generated tests, which create records without it" if required else ""),
        "Replace the TODO description: the generated docs show it",
    ]
    if renamed:
        result.next_steps.insert(
            0,
            f"The relation is named {renamed!r}: the entity already has a "
            f"{field_name.removesuffix('_id')!r} field (pass --relation to choose another)",
        )
    result.next_steps = [s for s in result.next_steps if s]
    return result


def _entity_fields(base: Path, target: _ResourceFile) -> list[str]:
    entity = _entity_class(target)
    path = base / "domain/entities.py"
    if entity is None or not path.exists():
        return []
    try:
        return edit.class_fields(path.read_text(encoding="utf-8"), entity)
    except edit.EditError:
        return []


def _added_field(
    base: Path,
    package: str,
    target: _ResourceFile,
    field_name: str,
    required: bool,
    result: Result,
    *,
    dry_run: bool,
) -> bool:
    """Add ``field_name: int`` to the entity, the schemas and the table when
    the scaffolded files are there and lack it. Whether it was added."""
    entities = base / "domain/entities.py"
    schemas = base / "application/schemas.py"
    persistence = base / "infrastructure/persistence.py"
    entity = _entity_class(target)
    if entity is None or not entities.exists():
        return False
    source = entities.read_text(encoding="utf-8")
    try:
        if field_name in edit.class_fields(source, entity):
            return False
    except edit.EditError:
        return False
    annotation = "int" if required else "int | None"
    default = "" if required else " = None"
    source = edit.add_field(
        source,
        entity,
        f"{field_name}: {annotation}{default}",
        required=required,
        before=("status", "version", "id"),
    )
    _save(entities, source, result, dry_run=dry_run)
    if schemas.exists():
        text = schemas.read_text(encoding="utf-8")
        for cls, line in (
            (f"{entity}Create", f"{field_name}: {annotation}{default}"),
            (f"{entity}Update", f"{field_name}: int | None = None"),
            (f"{entity}Read", f"{field_name}: {annotation}"),
        ):
            try:
                if field_name not in edit.class_fields(text, cls):
                    text = edit.add_field(text, cls, line, required=False)
            except edit.EditError:
                continue
        _save(schemas, text, result, dry_run=dry_run)
    if persistence.exists():
        text = persistence.read_text(encoding="utf-8")
        table = f"{target.resource}_table"
        try:
            text = edit.add_call_argument(
                text, table, f'Column("{field_name}", Integer, nullable={not required})'
            )
            text = edit.add_import(text, "sqlalchemy", "Integer")
        except edit.EditError:
            pass
        else:
            _save(persistence, text, result, dry_run=dry_run)
    return True


def _entity_class(target: _ResourceFile) -> str | None:
    model = target.attribute("model")
    return model.id if isinstance(model, ast.Name) else None


# ----------------------------------------------------------------- policy


def policy(
    root: Path,
    package: str,
    resource: str,
    role: str,
    *,
    read_only: bool = False,
    name: str | None = None,
    variable: str = "SEED_POLICIES",
    dry_run: bool = False,
) -> Result:
    """A seed policy granting ``role`` the resource's operations, appended to
    ``<package>/authz.py``. The actions come from the registered resource."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", role):
        raise ScaffoldError(f"Invalid role {role!r}")
    if not variable.isidentifier():
        raise ScaffoldError(f"Invalid variable name {variable!r}")
    base = _existing(root, package)
    target = _ResourceFile(base, package, resource)
    actions = _actions(root, target, read_only=read_only)
    name = name or (
        f"{pascal(role)} reads {resource}" if read_only else f"{pascal(role)} manages {resource}"
    )
    path = base / "authz.py"
    source = _read(
        path,
        '"""Seed policies: created at start-up and checked by resource-engine check."""\n\n'
        "from __future__ import annotations\n\nfrom typing import Any\n",
    )
    node = edit.module_variable(source, variable)
    if node is None:
        source = source.rstrip("\n") + f"\n\n{variable}: list[dict[str, Any]] = []\n"
        source = edit.add_import(source, "typing", "Any")
        node = edit.module_variable(source, variable)
    if not isinstance(node.value, ast.List):  # type: ignore[union-attr]
        raise ScaffoldError(
            f"{path}: {variable} is not a list literal; pass --variable to add a new list"
        )
    existing = [
        e.values[edit.dict_keys(e).index("name")].value  # type: ignore[attr-defined]
        for e in node.value.elts  # type: ignore[union-attr]
        if isinstance(e, ast.Dict) and "name" in edit.dict_keys(e)
    ]
    if name in existing:
        raise ScaffoldError(f"{path} already has a policy named {name!r}")
    entry = (
        "{\n"
        f'    "name": {_string(name)},\n'
        '    "effect": "allow",\n'
        f'    "subjects": {{"roles": [{_string(role)}]}},\n'
        '    "actions": [\n' + "".join(f"        {_string(a)},\n" for a in actions) + "    ],\n"
        "}"
    )
    try:
        source = edit.append_to_module_list(source, variable, entry)
    except edit.EditError as exc:
        raise ScaffoldError(f"{path}: {exc}") from None
    result = Result()
    _save(path, source, result, dry_run=dry_run)
    result.next_steps = [
        f'Point check at the seed: [tool.resource-engine] policies = "{package}.authz:{variable}"',
        "Create the seed at start-up (manager.create('authz_policies', policy) for the ones "
        "missing), as the example's bootstrap does",
        "Narrow it with a condition when the role should only see some records "
        '("condition": {"eq": [{"attr": "resource.status"}, "draft"]})',
    ]
    return result


def _actions(root: Path, target: _ResourceFile, *, read_only: bool) -> list[str]:
    from resource_engine import ResourceRegistry
    from resource_engine.authz.catalog import Catalog

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    for module in [
        m for m in sys.modules if m == target.package or m.startswith(target.package + ".")
    ]:
        del sys.modules[module]
    try:
        cls = getattr(importlib.import_module(target.module), target.class_name)
        registry = ResourceRegistry()
        registry.register(cls)
    except Exception as exc:  # noqa: BLE001 - report any import or registration error
        raise ScaffoldError(f"Cannot load {target.module}.{target.class_name}: {exc}") from None
    reading = {"list", "get", "download"}
    kinds = {"crud", "transition", "action", "attach", "detach", "download"}
    names = []
    for info in Catalog._actions(next(iter(registry))):
        if info.kind not in kinds:
            continue
        operation = info.name.split(":")[1]
        if read_only and operation not in reading:
            continue
        names.append(info.name)
    return names
