"""``resource-engine scaffold``: deterministic boilerplate for a module, an action
or a state machine, laid out like the reference module (``examples/invoices``).

The generated code imports, registers and passes ``check`` and its own tests
as it is. Every docstring starts with ``TODO:``; ``docs validate`` warns about
them (RE-D010) until they describe the business. Existing files are never
overwritten without ``force``.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

#: Field type → (Python annotation, SQLAlchemy column type, import of the annotation).
TYPES: dict[str, tuple[str, str, str | None]] = {
    "str": ("str", "String(200)", None),
    "text": ("str", "Text", None),
    "int": ("int", "Integer", None),
    "bool": ("bool", "Boolean", None),
    "Decimal": ("Decimal", "Numeric(12, 2, asdecimal=True)", "from decimal import Decimal"),
    "date": ("date", "Date", "from datetime import date"),
    "datetime": ("datetime", "DateTime(timezone=True)", "from datetime import datetime"),
}

#: Values used in generated tests, per type.
SAMPLES = {
    "str": '"Sample"',
    "text": '"Sample text"',
    "int": "1",
    "bool": "True",
    "Decimal": '"10.00"',
    "date": '"2026-01-31"',
    "datetime": '"2026-01-31T10:00:00+00:00"',
}


class ScaffoldError(SystemExit):
    """A bad argument or a file that already exists (exit status 2)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = 2
        self.message = message


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    optional: bool = False

    @property
    def annotation(self) -> str:
        base = TYPES[self.type][0]
        return f"{base} | None" if self.optional else base

    @property
    def column(self) -> str:
        return TYPES[self.type][1]


@dataclass(frozen=True)
class Transition:
    event: str
    source: str
    target: str


@dataclass
class Result:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {"written": self.written, "skipped": self.skipped, "next_steps": self.next_steps}


# ----------------------------------------------------------------- parsing


def parse_fields(raw: list[str] | None) -> list[Field]:
    fields = []
    for item in raw or []:
        name, _, kind = item.partition(":")
        optional = kind.endswith("?")
        kind = kind.rstrip("?") or "str"
        _identifier(name, "field")
        if kind not in TYPES:
            raise ScaffoldError(
                f"Unknown type {kind!r} for {name!r}; use one of: {', '.join(TYPES)}"
            )
        if name in ("id", "status", "version"):
            raise ScaffoldError(f"{name!r} is generated; don't pass it as a field")
        fields.append(Field(name, kind, optional))
    if len({f.name for f in fields}) != len(fields):
        raise ScaffoldError("Duplicate field names")
    return fields


def parse_machine(
    states: str | None, transitions: str | None
) -> tuple[list[str], list[Transition]]:
    names = [s.strip() for s in (states or "draft,active,closed").split(",") if s.strip()]
    for name in names:
        _identifier(name, "state")
    if len(names) < 2:
        raise ScaffoldError("A state machine needs at least two states and one transition")
    raw = transitions or ",".join(
        f"{_verb(b)}:{a}>{b}" for a, b in zip(names, names[1:], strict=False)
    )
    result = []
    for item in raw.split(","):
        match = re.fullmatch(r"\s*(\w+)\s*:\s*(\w+)\s*>\s*(\w+)\s*", item)
        if not match:
            raise ScaffoldError(f"Bad transition {item!r}; use event:source>target")
        event, source, target = match.groups()
        for state in (source, target):
            if state not in names:
                raise ScaffoldError(f"Transition {event!r} uses unknown state {state!r}")
        _identifier(event, "transition")
        result.append(Transition(event, source, target))
    if len(names) < 2 or not result:
        raise ScaffoldError("A state machine needs at least two states and one transition")
    clash = {t.event for t in result} & set(names)
    if clash:
        raise ScaffoldError(f"Names used both as state and transition: {', '.join(sorted(clash))}")
    return names, result


def _verb(state: str) -> str:
    known = {"active": "activate", "closed": "close", "approved": "approve", "done": "finish"}
    return known.get(state, f"to_{state}")


def _identifier(name: str, what: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name) or not name.islower():
        raise ScaffoldError(f"Invalid {what} name {name!r}: use a lowercase Python identifier")


def pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ses"):
        return name[:-2]
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def package_dir(root: Path, package: str) -> Path:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*", package):
        raise ScaffoldError(f"Invalid package {package!r}: use a dotted lowercase name")
    return root.joinpath(*package.split("."))


# ----------------------------------------------------------------- writing


def _write(path: Path, content: str, result: Result, *, force: bool, dry_run: bool) -> None:
    if path.exists() and not force:
        result.skipped.append(str(path))
        return
    result.written.append(str(path))
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _render(template: str, **values: str) -> str:
    text = Template(template).substitute(values)
    return re.sub(r"\n{3,}", "\n\n\n", text).strip("\n") + "\n"


# ---------------------------------------------------------------- resource


def resource(
    root: Path,
    package: str,
    name: str,
    fields: list[Field],
    *,
    entity: str | None = None,
    machine: tuple[list[str], list[Transition]] | None = None,
    versioned: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    _identifier(name, "resource")
    entity = entity or pascal(singular(name))
    if not entity.isidentifier() or not entity[0].isupper():
        raise ScaffoldError(f"Invalid entity class name {entity!r}")
    base = package_dir(root, package)
    if base.exists() and any(base.iterdir()) and not force:
        raise ScaffoldError(f"{base} already exists; pick another package or pass --force")
    result = Result()
    if not fields:
        fields = [Field("name", "str")]
    ctx = _Context(package, name, entity, fields, machine, versioned)
    files = {
        "__init__.py": f'"""{entity} module."""\n',
        "domain/__init__.py": "",
        "domain/entities.py": ctx.entities(),
        "application/__init__.py": "",
        "application/schemas.py": ctx.schemas(),
        "application/hooks.py": ctx.hooks(),
        "application/resource.py": ctx.resource(),
        "infrastructure/__init__.py": "",
        "infrastructure/persistence.py": ctx.persistence(),
        f"tests/test_{name}.py": ctx.tests(),
    }
    if machine:
        files["domain/state_machine.py"] = state_machine_source(entity, *machine)
    for relative, content in files.items():
        _write(base / relative, content, result, force=force, dry_run=dry_run)
    cls = f"{entity}Resource"
    result.next_steps = [
        f"Register it in your composition root: from {package}.application.resource import {cls}; "
        f"registry.register({cls})",
        f"Map it at start-up: from {package}.infrastructure.persistence import map_entities; "
        "map_entities() (and add its metadata to your migrations)",
        f"Give every operation a gate: a seed policy or a system role for {name}:list, "
        f"{name}:get, {name}:create, {name}:update, {name}:delete"
        + "".join(
            f", {name}:transition:{event}"
            for event in dict.fromkeys(t.event for t in (machine[1] if machine else []))
        )
        + " (resource-engine check reports the rest as RE-C004)",
        "Replace every TODO docstring and the TODO rule in the entity; then run "
        "resource-engine check, resource-engine docs validate --strict and pytest",
    ]
    return result


@dataclass
class _Context:
    package: str
    name: str
    entity: str
    fields: list[Field]
    machine: tuple[list[str], list[Transition]] | None
    versioned: bool

    @property
    def imports(self) -> list[str]:
        return sorted(
            {TYPES[f.type][2] for f in self.fields if TYPES[f.type][2]}  # type: ignore[misc]
        )

    def entities(self) -> str:
        required = [f for f in self.fields if not f.optional]
        optional = [f for f in self.fields if f.optional]
        lines = [f"    {f.name}: {f.annotation}" for f in required]
        lines += [f"    {f.name}: {f.annotation} = None" for f in optional]
        if self.machine:
            lines.append("    status: str | None = None")
        if self.versioned:
            lines.append("    version: int | None = None")
        lines.append("    id: int | None = None")
        return _render(
            '''
from __future__ import annotations

from dataclasses import dataclass$imports

from resource_engine.domain import DomainError


class ${entity}Error(DomainError):
    code = "$code"


@dataclass
class $entity:
    """TODO: describe what a $label is.

    Business Rules:
        - TODO: list the rules a $label must keep.
    """

$fields

    def ensure_valid(self) -> None:
        """TODO: enforce the business rules; raise ${entity}Error when one is broken."""
''',
            entity=self.entity,
            code=self.name.upper(),
            label=self.name.removesuffix("s").replace("_", " "),
            imports="".join(f"\n{line}" for line in self.imports),
            fields="\n".join(lines),
        )

    def schemas(self) -> str:
        create = [
            f"    {f.name}: {f.annotation}" + (" = None" if f.optional else "") for f in self.fields
        ]
        update = [f"    {f.name}: {TYPES[f.type][0]} | None = None" for f in self.fields]
        read = [f"    {f.name}: {f.annotation}" for f in self.fields]
        if self.machine:
            read.append("    status: str")
        if self.versioned:
            read.append("    version: int")
        imports = "\n".join(self.imports)
        return _render(
            '''
from __future__ import annotations$imports

from pydantic import BaseModel, ConfigDict


class ${entity}Create(BaseModel):
    """TODO: data needed to create a $label."""

$create


class ${entity}Update(BaseModel):
    """TODO: fields that can be changed."""

$update


class ${entity}Read(BaseModel):
    """TODO: what clients see of a $label."""

    model_config = ConfigDict(from_attributes=True)

    id: int
$read
''',
            entity=self.entity,
            label=self.name.removesuffix("s").replace("_", " "),
            imports=("\n\n" + imports) if imports else "",
            create="\n".join(create),
            update="\n".join(update),
            read="\n".join(read),
        )

    def hooks(self) -> str:
        return _render(
            '''
from __future__ import annotations

from resource_engine import OperationContext


def validate(ctx: OperationContext) -> None:
    """TODO: say which rules are checked before the $label is saved."""
    ctx.entity.ensure_valid()
''',
            label=self.name.removesuffix("s").replace("_", " "),
        )

    def resource(self) -> str:
        machine_import = (
            f"from {self.package}.domain.state_machine import {self.entity}StateMachine\n"
            if self.machine
            else ""
        )
        options = []
        if self.machine:
            options.append(f"    state_machine = {self.entity}StateMachine")
        if self.versioned:
            options.append('    version_field = "version"')
        return _render(
            '''
from __future__ import annotations

from resource_engine import Resource

from $package.application import hooks
from $package.application.schemas import ${entity}Create, ${entity}Read, ${entity}Update
from $package.domain.entities import $entity
$machine_import

class ${entity}Resource(Resource):
    """TODO: describe what this resource lets users do.

    Business area:
        TODO
    """

    name = "$name"
    model = $entity
    schemas = {"create": ${entity}Create, "update": ${entity}Update, "read": ${entity}Read}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "TODO: describe the list.",
        "get": "TODO: describe what is returned.",
        "create": "TODO: describe the creation.",
        "update": "TODO: describe what can be changed.",
        "delete": "TODO: describe the deletion.",
    }
    hooks = {"before_create": [hooks.validate], "after_update": [hooks.validate]}
    sortable = ["id"]
$options
''',
            package=self.package,
            entity=self.entity,
            name=self.name,
            machine_import=machine_import,
            options="\n".join(options),
        )

    def persistence(self) -> str:
        columns = [
            f'    Column("{f.name}", {f.column}, nullable={f.optional}),' for f in self.fields
        ]
        if self.machine:
            columns.append('    Column("status", String(32), nullable=False),')
        if self.versioned:
            columns.append('    Column("version", Integer, nullable=False, default=1),')
        used = {"Column", "Integer", "MetaData", "Table"}
        for f in self.fields:
            used.add(f.column.split("(")[0])
        if self.machine:
            used.add("String")
        return _render(
            '''
"""SQLAlchemy mapping of the $name module. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import $sa_imports
from sqlalchemy.orm import registry as orm_registry

from $package.domain.entities import $entity

metadata = MetaData()
mapper_registry = orm_registry(metadata=metadata)

${name}_table = Table(
    "$name",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
$columns
)

_mapped = False


def map_entities() -> None:
    """Idempotently map the domain entities to their tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively($entity, ${name}_table)
        _mapped = True
''',
            name=self.name,
            package=self.package,
            entity=self.entity,
            sa_imports=", ".join(sorted(used)),
            columns="\n".join(columns),
        )

    def tests(self) -> str:
        payload = ", ".join(f'"{f.name}": {SAMPLES[f.type]}' for f in self.fields if not f.optional)
        first = self.fields[0]
        changed = {
            "str": '"Changed"',
            "text": '"Changed"',
            "int": "2",
            "bool": "False",
            "Decimal": '"20.00"',
            "date": '"2026-02-28"',
            "datetime": '"2026-02-28T10:00:00+00:00"',
        }[first.type]
        machine_test = ""
        if self.machine:
            states, transitions = self.machine
            t = next(x for x in transitions if x.source == states[0])
            machine_test = f'''

async def test_starts_in_{states[0]}_and_{t.event}(manager):
    created = await manager.create("{self.name}", {{{payload}}})
    assert created.status == "{states[0]}"
    moved = await manager.transition("{self.name}", created.id, "{t.event}")
    assert moved.status == "{t.target}"
'''
        return _render(
            '''
"""Lifecycle tests of the $name module against the in-memory adapters."""

from __future__ import annotations

import pytest
from resource_engine import ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore, RecordingEventPublisher

from $package.application.resource import ${entity}Resource

pytestmark = pytest.mark.asyncio


@pytest.fixture
def publisher():
    return RecordingEventPublisher()


@pytest.fixture
def manager(publisher):
    registry = ResourceRegistry()
    registry.register(${entity}Resource)
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(registry, store.unit_of_work, event_publisher=publisher)


async def test_create_get_update_delete(manager, publisher):
    created = await manager.create("$name", {$payload})
    assert (await manager.get("$name", created.id)).id == created.id
    updated = await manager.update("$name", created.id, {"$first": $changed})
    assert updated.$first != created.$first
    await manager.delete("$name", created.id)
    assert (await manager.list("$name")).total == 0
    assert publisher.names() == ["ResourceCreated", "ResourceUpdated", "ResourceDeleted"]
$machine_test
''',
            name=self.name,
            package=self.package,
            entity=self.entity,
            payload=payload,
            first=first.name,
            changed=changed,
            machine_test=machine_test,
        )


# ----------------------------------------------------------- state machine


def state_machine_source(entity: str, states: list[str], transitions: list[Transition]) -> str:
    state_lines = []
    for i, state in enumerate(states):
        flags = []
        if i == 0:
            flags.append("initial=True")
        if not any(t.source == state for t in transitions):
            flags.append("final=True")
        state_lines.append(
            f'    {state} = State("{pascal(state)}"' + "".join(f", {f}" for f in flags) + ")"
        )
    return _render(
        '''
from __future__ import annotations

from statemachine import State, StateMachine


class ${entity}StateMachine(StateMachine):
    """TODO: describe the process.

    States:
$state_docs

    Transitions:
$transition_docs
    """

$states

$transitions
''',
        entity=entity,
        state_docs="\n".join(f"        {s}: TODO" for s in states),
        transition_docs="\n".join(
            f"        {e}: TODO" for e in dict.fromkeys(t.event for t in transitions)
        ),
        states="\n".join(state_lines),
        transitions="\n".join(
            f"    {event} = "
            + " | ".join(f"{t.source}.to({t.target})" for t in transitions if t.event == event)
            for event in dict.fromkeys(t.event for t in transitions)
        ),
    )


def state_machine(
    root: Path,
    package: str,
    entity: str,
    states: list[str],
    transitions: list[Transition],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    base = _existing(root, package)
    result = Result()
    _write(
        base / "domain/state_machine.py",
        state_machine_source(entity, states, transitions),
        result,
        force=force,
        dry_run=dry_run,
    )
    if result.skipped:
        raise ScaffoldError(f"{result.skipped[0]} already exists; pass --force to replace it")
    result.next_steps = [
        f"On the resource: from {package}.domain.state_machine import {entity}StateMachine; "
        f"state_machine = {entity}StateMachine",
        "Add `status: str | None = None` to the entity, `status: str` to the read schema and a "
        "status column to the table; never put it in the create or update schema",
        "Grant the transitions: "
        + ", ".join(
            f"<resource>:transition:{e}" for e in dict.fromkeys(t.event for t in transitions)
        ),
        "Describe every state and transition in the docstring (replace the TODOs)",
    ]
    return result


# ------------------------------------------------------------------ action


def action(
    root: Path,
    package: str,
    resource_name: str,
    class_name: str,
    fields: list[Field],
    *,
    collection: bool = False,
    dry_run: bool = False,
) -> Result:
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", class_name):
        raise ScaffoldError(f"Invalid action class name {class_name!r}: use PascalCase")
    base = _existing(root, package)
    actions_path = base / "application/actions.py"
    schemas_path = base / "application/schemas.py"
    current = actions_path.read_text(encoding="utf-8") if actions_path.exists() else ""
    if re.search(rf"^class {class_name}\b", current, re.M):
        raise ScaffoldError(f"{actions_path} already defines {class_name}")
    action_name = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()
    input_cls = f"{class_name}Input" if fields else None
    result = Result()
    header = (
        "from __future__ import annotations\n\n"
        "from resource_engine import Action, OperationContext\n"
    )
    if input_cls:
        header += f"\nfrom {package}.application.schemas import {input_cls}\n"
    body = _render(
        '''
class $cls(Action):
    """TODO: say what the action does.

    Preconditions:
        TODO

    Postconditions:
        TODO
    """
$options
    async def execute(self, ctx: OperationContext) -> None:
        """TODO: implement; runs inside the transaction (no external effects)."""
        $body
''',
        cls=class_name,
        options=(
            "\n"
            + "\n".join(
                ([f"    input_schema = {input_cls}"] if input_cls else [])
                + (["    instance = False"] if collection else [])
            )
            + "\n"
        )
        if input_cls or collection
        else "",
        body="raise NotImplementedError"
        if collection
        else (
            "entity = ctx.entity  # loaded and authorized\n"
            "        await ctx.repository().update(entity)"
        ),
    )
    if current:
        content = current.rstrip("\n") + "\n\n\n" + body
        if "from resource_engine import" not in current:
            content = header + "\n\n" + content
        elif input_cls and f"import {input_cls}" not in current:
            content = content.replace(
                "from resource_engine import Action, OperationContext\n",
                "from resource_engine import Action, OperationContext\n\n"
                f"from {package}.application.schemas import {input_cls}\n",
                1,
            )
    else:
        content = header + "\n\n" + body
    result.written.append(str(actions_path))
    if not dry_run:
        actions_path.parent.mkdir(parents=True, exist_ok=True)
        actions_path.write_text(content, encoding="utf-8")
    if input_cls:
        schema = _render(
            '''
class $cls(BaseModel):
    """TODO: input of the action."""

$fields
''',
            cls=input_cls,
            fields="\n".join(
                f"    {f.name}: {f.annotation}" + (" = None" if f.optional else "") for f in fields
            ),
        )
        existing = schemas_path.read_text(encoding="utf-8") if schemas_path.exists() else ""
        if not existing:
            existing = "from __future__ import annotations\n\nfrom pydantic import BaseModel\n"
        extra = [
            TYPES[f.type][2]
            for f in fields
            if TYPES[f.type][2] and TYPES[f.type][2] not in existing
        ]
        if extra:
            existing = existing.replace(
                "from __future__ import annotations\n",
                "from __future__ import annotations\n\n" + "\n".join(sorted(set(extra))) + "\n",
                1,
            )
        result.written.append(str(schemas_path))
        if not dry_run:
            schemas_path.write_text(existing.rstrip("\n") + "\n\n\n" + schema, encoding="utf-8")
    declared = _declare_action(base, package, resource_name, class_name, result, dry_run)
    result.next_steps = [
        *(
            []
            if declared
            else [
                f"On the resource: from {package}.application.actions import {class_name}; "
                f"add {class_name} to `actions`"
            ]
        ),
        f"Grant it: {resource_name}:action:{action_name} in a seed policy or a system role",
        f"HTTP: POST /{resource_name}"
        + ("" if collection else "/{id}")
        + f"/actions/{action_name}",
        "Implement execute and replace the TODO docstring (Postconditions are required)",
    ]
    return result


def _declare_action(
    base: Path, package: str, resource: str, class_name: str, result: Result, dry_run: bool
) -> bool:
    """Add the action to the resource's ``actions``; ``False`` when the
    resource class isn't found in the module."""
    from resource_engine_devtools import source_edit as edit
    from resource_engine_devtools.scaffold_parts import _ResourceFile

    try:
        target = _ResourceFile(base, package, resource)
    except ScaffoldError:
        return False
    target.apply(lambda s: edit.add_import(s, f"{package}.application.actions", class_name))
    target.apply(lambda s: edit.append_to_list(s, target.class_name, "actions", class_name))
    target.write(result, dry_run=dry_run)
    return True


def _existing(root: Path, package: str) -> Path:
    base = package_dir(root, package)
    if not (base / "application").is_dir() and not (base / "domain").is_dir():
        raise ScaffoldError(f"{base} is not a module (no domain/ or application/)")
    return base
