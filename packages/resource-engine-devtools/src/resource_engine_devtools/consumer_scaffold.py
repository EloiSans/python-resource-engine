"""``resource-engine scaffold consumer``: a typed consumer from a provider's AsyncAPI.

Reads an AsyncAPI 3.0 or 2.x document (JSON, or YAML with PyYAML installed),
picks the messages the provider sends, and writes:

- ``<package>/consumers/<name>.py``: a Pydantic model per message payload
  (from its JSON Schema) and ``build_router(manager)``, an ``EventRouter`` with
  one typed handler stub per message;
- ``<package>/tests/contracts/<name>.asyncapi.json``: the provider's document
  as JSON, the contract the consumer was written against;
- ``<package>/tests/test_<name>_consumer.py``: the router keeps the contract
  (``assert_consumer_contract``; ``<NAME>_ASYNCAPI`` points it at a newer
  document), each sample payload parses, and each handler is still a stub.

Messages in the engine's envelope or in CloudEvents are unwrapped to their
payload (``payload`` / ``data``). Other bodies are modelled whole, and the
next steps say a decoder is needed. Handlers raise ``NotImplementedError``
until they are written, so messages are retried and dead-lettered instead of
silently acknowledged. Existing files are never overwritten without ``force``.
"""

from __future__ import annotations

import contextlib
import json
import keyword
import re
import subprocess
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from resource_engine_devtools.scaffold import (
    Result,
    ScaffoldError,
    _identifier,
    _write,
    package_dir,
)

# ------------------------------------------------------------------ reading


def load_document(path: Path) -> dict[str, Any]:
    """An AsyncAPI document from a JSON or YAML file."""
    if not path.is_file():
        raise ScaffoldError(f"No AsyncAPI document at {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ScaffoldError(
                "Reading YAML needs PyYAML: pip install pyyaml (or pass the JSON document)"
            ) from None
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ScaffoldError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(data, dict) or "asyncapi" not in data:
        raise ScaffoldError(f"{path} is not an AsyncAPI document (no 'asyncapi' key)")
    major = str(data["asyncapi"]).split(".")[0]
    if major not in ("2", "3"):
        raise ScaffoldError(f"AsyncAPI {data['asyncapi']} is not supported (2.x and 3.x are)")
    return data


@dataclass
class Message:
    """A message the provider sends, unwrapped to its payload."""

    event_type: str
    channel: str
    summary: str | None
    payload: dict[str, Any]
    wire: str  # envelope | cloudevents | raw
    examples: list[Any] = field(default_factory=list)


class _Resolver:
    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document

    def resolve(self, node: Any, depth: int = 0) -> Any:
        """``node`` with a top-level ``$ref`` (inside the document) followed."""
        while isinstance(node, Mapping) and isinstance(node.get("$ref"), str) and depth < 20:
            node = self.pointer(node["$ref"])
            depth += 1
        return node

    def pointer(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise ScaffoldError(f"Only references inside the document are supported, not {ref!r}")
        node: Any = self.document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, Mapping) or part not in node:
                raise ScaffoldError(f"Dangling reference {ref!r}")
            node = node[part]
        return node


def provider_messages(
    document: Mapping[str, Any],
    *,
    channels: Sequence[str] = (),
    messages: Sequence[str] = (),
    type_prefix: str = "",
    unique: bool = True,
) -> list[Message]:
    """The messages the provider sends (3.0 ``send`` operations, 2.x
    ``subscribe`` operations; every channel message when none is marked),
    optionally limited to some channels or message names. ``unique``: the
    first message of each event type only (types repeat across channels)."""
    resolver = _Resolver(document)
    found: list[tuple[str, Any]] = []
    if str(document["asyncapi"]).startswith("3"):
        operations = document.get("operations") or {}
        sends = [op for op in operations.values() if resolver.resolve(op).get("action") == "send"]
        if sends:
            for op in sends:
                op = resolver.resolve(op)
                channel_ref = (op.get("channel") or {}).get("$ref", "")
                channel = channel_ref.rsplit("/", 1)[-1]
                refs = op.get("messages") or list(
                    (resolver.resolve(op.get("channel") or {}).get("messages") or {}).values()
                )
                found += [(channel, m) for m in refs]
        else:
            for name, ch in (document.get("channels") or {}).items():
                found += [(name, m) for m in (resolver.resolve(ch).get("messages") or {}).values()]
    else:
        for name, ch in (document.get("channels") or {}).items():
            ch = resolver.resolve(ch)
            op = ch.get("subscribe") or ch.get("publish")
            if op is None:
                continue
            message = op.get("message") or {}
            parts = message.get("oneOf") if isinstance(message, Mapping) else None
            found += [(name, m) for m in (parts or [message])]
    out: list[Message] = []
    seen: set[str] = set()
    for channel, ref in found:
        if channels and channel not in channels:
            continue
        message = resolver.resolve(ref)
        unwrapped = _unwrap(resolver, message, type_prefix)
        if unwrapped is None:
            continue
        event_type, payload, wire = unwrapped
        if messages and event_type not in messages:
            continue
        key = event_type if unique else f"{channel}\x00{event_type}"
        if key in seen:
            continue
        seen.add(key)
        examples = [
            e.get("payload") if isinstance(e, Mapping) and "payload" in e else e
            for e in message.get("examples") or []
        ]
        out.append(
            Message(
                event_type=event_type,
                channel=channel,
                summary=message.get("summary")
                or message.get("title")
                or message.get("description"),
                payload=payload,
                wire=wire,
                examples=[_inner(e, wire) for e in examples if e is not None],
            )
        )
    if not out:
        raise ScaffoldError(
            "No messages to consume in the document"
            + (f" for channels {list(channels)}" if channels else "")
            + (f" named {list(messages)}" if messages else "")
        )
    return out


def _inner(example: Any, wire: str) -> Any:
    if isinstance(example, Mapping):
        if wire == "envelope" and "payload" in example:
            return example["payload"]
        if wire == "cloudevents" and "data" in example:
            return example["data"]
    return example


def _unwrap(
    resolver: _Resolver, message: Mapping[str, Any], type_prefix: str
) -> tuple[str, dict[str, Any], str] | None:
    schema = resolver.resolve(message.get("payload") or {})
    if isinstance(schema, Mapping) and "schema" in schema and "schemaFormat" in schema:
        schema = resolver.resolve(schema["schema"])  # multi-format schema object
    if not isinstance(schema, Mapping):
        return None
    props = schema.get("properties") or {}
    name = message.get("name") or message.get("title") or message.get("messageId")
    if "specversion" in props and "data" in props:
        event_type = (props.get("type") or {}).get("const") or name
        if event_type and type_prefix and event_type.startswith(type_prefix):
            event_type = event_type[len(type_prefix) :]
        return event_type, dict(resolver.resolve(props["data"])), "cloudevents"
    if "payload" in props and "type" in props:
        event_type = (props.get("type") or {}).get("const") or name
        return event_type, dict(resolver.resolve(props["payload"])), "envelope"
    if not name:
        return None
    return str(name), dict(schema), "raw"


# ------------------------------------------------------------- generation


_SCALARS = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
_FORMATS = {
    "date-time": ("datetime", "from datetime import datetime"),
    "date": ("date", "from datetime import date"),
    "time": ("time", "from datetime import time"),
    "uuid": ("UUID", "from uuid import UUID"),
    "decimal": ("Decimal", "from decimal import Decimal"),
}
_SAMPLE_FORMATS = {
    "date-time": "2026-01-31T10:00:00+00:00",
    "date": "2026-01-31",
    "time": "10:00:00",
    "uuid": "00000000-0000-0000-0000-000000000001",
    "email": "someone@example.com",
    "uri": "https://example.com",
    "decimal": "10.00",
}


def snake(name: str) -> str:
    text = re.sub(r"[^0-9A-Za-z]+", "_", name)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text).strip("_").lower()
    if not text or text[0].isdigit():
        text = f"field_{text}"
    return f"{text}_" if keyword.iskeyword(text) else text


def class_name(name: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", name)
    text = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not text or text[0].isdigit():
        text = f"Model{text}"
    return text


class _Models:
    """JSON Schema → Pydantic model source (classes in dependency order)."""

    def __init__(self, resolver: _Resolver) -> None:
        self.resolver = resolver
        self.classes: dict[str, str] = {}
        self.by_ref: dict[str, str] = {}
        self.imports: set[str] = set()
        self.uses_any = False
        self.uses_literal = False

    def model(self, name: str, schema: Mapping[str, Any], doc: str | None = None) -> str:
        name = self._unique(class_name(name))
        self.classes[name] = ""  # reserve the name (recursion)
        schema = self._merged(schema)
        required = set(schema.get("required") or ())
        lines = [f"class {name}(BaseModel):"]
        text = doc or schema.get("description")
        lines.append(f'    """{_docstring(text) if text else "TODO: what this payload is."}"""')
        lines += ["", '    model_config = ConfigDict(extra="ignore", populate_by_name=True)']
        properties = schema.get("properties") or {}
        if properties:
            lines.append("")
        for prop, sub in properties.items():
            sub = sub if isinstance(sub, Mapping) else {}
            annotation = self.annotation(sub, f"{name}_{prop}")
            optional = prop not in required
            if optional and not annotation.endswith("| None") and annotation != "Any":
                annotation = f"{annotation} | None"
            attr = snake(prop)
            args = []
            if optional:
                default = self.resolver.resolve(sub).get("default") if sub else None
                args.append(f"default={_py(default)}" if _literal(default) else "default=None")
            if attr != prop:
                args.append(f"alias={_py(prop)}")
            description = self.resolver.resolve(sub).get("description") if sub else None
            if description:
                args.append(f"description={_py(_one_line(description))}")
            if args:
                lines.append(f"    {attr}: {annotation} = Field({', '.join(args)})")
            else:
                lines.append(f"    {attr}: {annotation}")
        if not properties:
            lines += ["", '    model_config = ConfigDict(extra="allow")']
            lines = [line for line in lines if "populate_by_name" not in line]
        self.classes[name] = "\n".join(lines)
        return name

    def _unique(self, name: str) -> str:
        candidate, n = name, 2
        while candidate in self.classes:
            candidate, n = f"{name}{n}", n + 1
        return candidate

    def _merged(self, schema: Mapping[str, Any]) -> dict[str, Any]:
        schema = dict(self.resolver.resolve(schema))
        for part in schema.pop("allOf", None) or []:
            part = self._merged(part)
            schema["properties"] = {
                **(part.get("properties") or {}),
                **(schema.get("properties") or {}),
            }
            schema["required"] = [*(part.get("required") or []), *(schema.get("required") or [])]
            if "type" not in schema and "type" in part:
                schema["type"] = part["type"]
        return schema

    def annotation(self, schema: Mapping[str, Any], hint: str) -> str:
        ref = schema.get("$ref") if isinstance(schema, Mapping) else None
        if isinstance(ref, str):
            if ref in self.by_ref:
                return self.by_ref[ref]
            target = self.resolver.pointer(ref)
            if self._is_object(target):
                name = class_name(target.get("title") or ref.rsplit("/", 1)[-1])
                self.by_ref[ref] = self._unique(name)
                self.by_ref[ref] = self.model(self.by_ref[ref], target)
                return self.by_ref[ref]
            return self.annotation(target, hint)
        schema = self._merged(schema)
        if "const" in schema:
            self.uses_literal = True
            return f"Literal[{_py(schema['const'])}]"
        if "enum" in schema and all(_literal(v) for v in schema["enum"]):
            self.uses_literal = True
            values = [v for v in schema["enum"] if v is not None]
            literal = f"Literal[{', '.join(_py(v) for v in values)}]"
            return f"{literal} | None" if None in schema["enum"] else literal
        for key in ("anyOf", "oneOf"):
            if key in schema:
                parts = [self.annotation(s, hint) for s in schema[key]]
                return _union(parts)
        kind = schema.get("type")
        if isinstance(kind, list):
            parts = [self.annotation({**schema, "type": k}, hint) for k in kind]
            return _union(parts)
        if kind == "null":
            return "None"
        if kind in _SCALARS:
            fmt = schema.get("format")
            if kind == "string" and fmt in _FORMATS:
                annotation, line = _FORMATS[fmt]
                self.imports.add(line)
                return annotation
            return _SCALARS[kind]
        if kind == "array":
            items = schema.get("items")
            if not isinstance(items, Mapping) or not items:
                self.uses_any = True
                return "list[Any]"
            return f"list[{self.annotation(items, hint)}]"
        if self._is_object(schema):
            if schema.get("properties"):
                return self.model(schema.get("title") or hint, schema)
            extra = schema.get("additionalProperties")
            if isinstance(extra, Mapping) and extra:
                return f"dict[str, {self.annotation(extra, hint)}]"
            self.uses_any = True
            return "dict[str, Any]"
        self.uses_any = True
        return "Any"

    @staticmethod
    def _is_object(schema: Any) -> bool:
        return isinstance(schema, Mapping) and (
            schema.get("type") == "object" or "properties" in schema or "allOf" in schema
        )

    def sample(self, schema: Any, depth: int = 0) -> Any:
        """A value valid for ``schema``, for the generated tests."""
        schema = self._merged(schema) if isinstance(schema, Mapping) else {}
        for key in ("examples",):
            if schema.get(key):
                return schema[key][0]
        for key in ("example", "default", "const"):
            if key in schema:
                return schema[key]
        if schema.get("enum"):
            return next((v for v in schema["enum"] if v is not None), None)
        for key in ("anyOf", "oneOf"):
            if schema.get(key):
                options = [s for s in schema[key] if self.resolver.resolve(s).get("type") != "null"]
                return self.sample(options[0] if options else schema[key][0], depth)
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "null")
        if kind == "string":
            return _SAMPLE_FORMATS.get(schema.get("format"), "sample")
        if kind == "integer":
            return max(1, int(schema.get("minimum", 1)))
        if kind == "number":
            return float(schema.get("minimum", 1))
        if kind == "boolean":
            return True
        if kind == "null":
            return None
        if kind == "array":
            return [self.sample(schema.get("items") or {}, depth + 1)] if depth < 4 else []
        if self._is_object(schema) and depth < 4:
            return {
                p: self.sample(s, depth + 1)
                for p, s in (schema.get("properties") or {}).items()
                if p in set(schema.get("required") or ())
            }
        return {}


def _union(parts: list[str]) -> str:
    unique: list[str] = []
    for part in parts:
        for piece in part.split(" | "):
            if piece not in unique:
                unique.append(piece)
    if "None" in unique:
        unique = [p for p in unique if p != "None"] + ["None"]
    return " | ".join(unique) if unique else "Any"


def _py(value: Any) -> str:
    """A Python literal, with double-quoted strings (as ruff formats them)."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _literal(value: Any) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _docstring(text: str) -> str:
    line = _one_line(text).replace('"""', "'''").replace("\\", "\\\\")
    return line if len(line) <= 80 else line[:77].rstrip() + "..."


# ---------------------------------------------------------------- scaffold


def consumer(
    root: Path,
    package: str,
    name: str,
    document_path: Path,
    *,
    channels: Sequence[str] = (),
    messages: Sequence[str] = (),
    type_prefix: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> Result:
    _identifier(name, "consumer")
    document = load_document(document_path)
    found = provider_messages(
        document, channels=channels, messages=messages, type_prefix=type_prefix
    )
    resolver = _Resolver(document)
    models = _Models(resolver)
    handlers: list[tuple[Message, str]] = []
    for message in found:
        if not message.event_type.replace(".", "").replace("-", "").replace("_", "").isalnum():
            raise ScaffoldError(f"Cannot name a model after event type {message.event_type!r}")
        payload_cls = models.model(message.event_type, message.payload, message.summary)
        handlers.append((message, payload_cls))

    info = document.get("info") or {}
    source = f"{info.get('title', document_path.name)} {info.get('version', '')}".strip()
    consumer_name = name.replace("_", "-")
    base = package_dir(root, package)
    module = f"{package}.consumers.{name}"
    result = Result()
    _write(
        base / "consumers" / "__init__.py",
        '"""Consumers of other services\' events."""\n',
        result,
        force=False,
        dry_run=dry_run,
    )
    if str(base / "consumers" / "__init__.py") in result.skipped:
        result.skipped.remove(str(base / "consumers" / "__init__.py"))
    _write(
        base / "consumers" / f"{name}.py",
        _module_source(name, consumer_name, source, document, models, handlers),
        result,
        force=force,
        dry_run=dry_run,
    )
    _write(
        base / "tests" / "contracts" / f"{name}.asyncapi.json",
        json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n",
        result,
        force=force,
        dry_run=dry_run,
    )
    _write(
        base / "tests" / f"test_{name}_consumer.py",
        _test_source(module, name, type_prefix, models, handlers),
        result,
        force=force,
        dry_run=dry_run,
    )
    if not dry_run:
        _format([p for p in result.written if p.endswith(".py")])
    result.next_steps = [
        "Write each handler: call the manager (an action, a transition) with a service "
        "principal as actor, and replace the TODO docstrings",
        f"Run it: MessageProcessor({module.rsplit('.', 1)[-1]}.build_router(manager), "
        f'name="{consumer_name}", manager=manager) with the broker\'s MessageSource '
        "(see docs/messaging.md#consumers)",
        f'Document it: add "{module}:build_router" to [tool.resource-engine] consumers, '
        "so resource-engine docs asyncapi lists what this service receives",
    ]
    raw = [m.event_type for m, _ in handlers if m.wire == "raw"]
    if raw:
        result.next_steps.append(
            f"{', '.join(raw)} {'is' if len(raw) == 1 else 'are'} neither an engine envelope nor "
            "a CloudEvent: write a decoder that builds an EventEnvelope "
            "(build_event_receiver(decode=...) or your MessageSource)"
        )
    return result


def _format(paths: list[str]) -> None:
    """``ruff format`` the generated files when ruff is installed (long lines)."""
    if not paths:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--quiet", "--line-length", "100", *paths],
            check=False,
            capture_output=True,
            timeout=60,
        )


def _module_source(
    name: str,
    consumer_name: str,
    source: str,
    document: Mapping[str, Any],
    models: _Models,
    handlers: list[tuple[Message, str]],
) -> str:
    imports = ["from __future__ import annotations", ""]
    stdlib = sorted(models.imports)
    typing_names = [
        n for n, used in (("Any", models.uses_any), ("Literal", models.uses_literal)) if used
    ]
    if typing_names:
        stdlib.append(f"from typing import {', '.join(typing_names)}")
    stdlib.sort(key=lambda line: line.split()[1])
    imports += stdlib
    if stdlib:
        imports.append("")
    imports += [
        "from pydantic import BaseModel, ConfigDict, Field",
        "from resource_engine import ResourceManager",
        "from resource_engine.messaging import EventEnvelope, EventRouter",
    ]
    if not any("Field(" in c for c in models.classes.values()):
        imports[imports.index("from pydantic import BaseModel, ConfigDict, Field")] = (
            "from pydantic import BaseModel, ConfigDict"
        )
    about = textwrap.fill(
        f"TODO: what this service does with the events of {_one_line(source)}.",
        width=88,
        subsequent_indent="",
    )
    header = (
        f'"""{about}\n\n'
        "Generated by ``resource-engine scaffold consumer`` from AsyncAPI "
        f"{document.get('asyncapi')};\nthe models follow the provider's schemas.\n"
        '"""'
    )
    body = [header, "", *imports, "", ""]
    body.append("\n\n\n".join(models.classes.values()))
    body += ["", "", ""]
    body.append(
        "def build_router(manager: ResourceManager | None = None) -> EventRouter:\n"
        f'    """Handlers for the ``{consumer_name}`` consumer. ``manager`` is ``None`` when\n'
        '    tools only describe the router (resource-engine docs asyncapi)."""\n'
        f"    router = EventRouter(\n"
        f'        name="{consumer_name}",\n'
        '        description="TODO: what this consumer does with these events.",\n'
        "    )\n"
    )
    for message, cls in handlers:
        fn = f"on_{snake(message.event_type)}"
        provider = (
            textwrap.indent(
                textwrap.fill(f"Provider: {_one_line(message.summary)}", width=80), " " * 8
            )
            if message.summary
            else ""
        )
        doc = f"TODO: what receiving {message.event_type} does."
        doc = f"{doc}\n\n{provider}\n        " if provider else doc
        body.append(
            f'    @router.on("{message.event_type}", payload={cls})\n'
            f"    async def {fn}(envelope: EventEnvelope, event: {cls}) -> None:\n"
            f'        """{doc}"""\n'
            f'        raise NotImplementedError("{fn} is not written yet")  # TODO\n'
        )
    body.append("    return router\n")
    return "\n".join(body).rstrip("\n") + "\n"


def _test_source(
    module: str, name: str, type_prefix: str, models: _Models, handlers: list[tuple[Message, str]]
) -> str:
    cases = []
    for message, _cls in handlers:
        sample = message.examples[0] if message.examples else models.sample(message.payload)
        cases.append((message.event_type, sample))
    rendered = ",\n".join(f"    ({t!r}, {json.dumps(s, ensure_ascii=False)})" for t, s in cases)
    rendered = re.sub(
        r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", rendered))
    )
    variable = f"{name.upper()}_ASYNCAPI"
    channels = sorted({m.channel for m, _ in handlers})
    prefix = f", channels={json.dumps(channels)}"
    prefix += f", type_prefix={_py(type_prefix)}" if type_prefix else ""
    return f'''"""Contract tests for {module}.

The router matches the provider's AsyncAPI document, the provider's sample
payloads parse, and handlers are still stubs. Point {variable} at the
provider's latest document (a file) to check it instead of the snapshot.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from resource_engine.messaging import EventEnvelope
from resource_engine_devtools.contracts import assert_consumer_contract

from {module} import build_router

CONTRACT = Path(__file__).parent / "contracts" / "{name}.asyncapi.json"

SAMPLES = [
{rendered},
]


def test_contract():
    document = os.environ.get("{variable}") or CONTRACT
    assert_consumer_contract(build_router(), document{prefix})


def _envelope(event_type, payload):
    return EventEnvelope(type=event_type, payload=payload, occurred_at=datetime.now(UTC))


@pytest.mark.parametrize(("event_type", "payload"), SAMPLES)
def test_samples_parse(event_type, payload):
    router = build_router()
    parsed = router.parse(_envelope(event_type, payload))
    assert isinstance(parsed, router.payload_type(event_type))


@pytest.mark.parametrize(("event_type", "payload"), SAMPLES)
async def test_handlers_are_written(event_type, payload):
    router = build_router()
    with pytest.raises(NotImplementedError):  # TODO: replace with the handler's effect
        await router.dispatch(_envelope(event_type, payload))
'''
