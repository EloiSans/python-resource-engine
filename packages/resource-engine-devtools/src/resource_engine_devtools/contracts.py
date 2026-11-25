"""Contract tests between services, from AsyncAPI documents.

- **Consumer side:** ``assert_consumer_contract(router, document)`` checks an
  ``EventRouter``'s typed handlers against the provider's AsyncAPI document.
  Every handled event is still sent. Every field the payload model requires is
  sent and required by the provider, with types the model accepts (enum values
  included). The provider's examples, and a sample built from its schema,
  parse. ``resource-engine scaffold consumer`` generates this test.
- **Provider side:** ``breaking_changes(old, new)`` compares two versions of
  the document an application publishes. It lists what would break existing
  consumers: a removed event or field, a required field made optional, a
  changed type, new enum values. ``resource-engine docs asyncapi --check FILE``
  runs it in CI.

See docs/contracts.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from resource_engine.errors import ResourceEngineError
from resource_engine.messaging import EventEnvelope, EventRouter

from resource_engine_devtools.consumer_scaffold import (
    Message,
    _Models,
    _Resolver,
    load_document,
    provider_messages,
)

MAX_DEPTH = 6


@dataclass(frozen=True)
class Problem:
    """One incompatibility: ``event_type``, the field ``path`` (``""`` for the
    message itself) and what is wrong."""

    event_type: str
    path: str
    message: str

    def __str__(self) -> str:
        where = f"{self.event_type}.{self.path}" if self.path else self.event_type
        return f"{where}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        return {"event_type": self.event_type, "path": self.path, "message": self.message}


def _document(document: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    return document if isinstance(document, Mapping) else load_document(Path(document))


def _messages(
    document: Mapping[str, Any], type_prefix: str, channels: Sequence[str] = ()
) -> dict[tuple[str, str], Message]:
    """The provider's messages by ``(channel, event type)``."""
    try:
        found = provider_messages(
            document, type_prefix=type_prefix, channels=channels, unique=False
        )
    except SystemExit:  # no messages at all
        return {}
    return {(m.channel, m.event_type): m for m in found}


# -------------------------------------------------------------- comparison


def _types(schema: Any, resolver: _Resolver, depth: int = 0) -> set[str] | None:
    """JSON types a schema allows (``None``: anything)."""
    schema = resolver.resolve(schema) if isinstance(schema, Mapping) else {}
    if not isinstance(schema, Mapping) or depth > MAX_DEPTH:
        return None
    if "const" in schema:
        return {_json_type(schema["const"])}
    if "enum" in schema:
        return {_json_type(v) for v in schema["enum"]}
    for key in ("anyOf", "oneOf"):
        if key in schema:
            out: set[str] = set()
            for part in schema[key]:
                part_types = _types(part, resolver, depth + 1)
                if part_types is None:
                    return None
                out |= part_types
            return out
    kind = schema.get("type")
    if isinstance(kind, list):
        return set(kind)
    if isinstance(kind, str):
        return {kind}
    if "properties" in schema or "allOf" in schema:
        return {"object"}
    return None


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _accepts(reader: set[str] | None, writer: set[str] | None) -> set[str]:
    """Writer types the reader does not accept (integers are numbers)."""
    if reader is None or writer is None:
        return set()
    accepted = reader | ({"integer"} if "number" in reader else set())
    return writer - accepted


def _enum(schema: Any, resolver: _Resolver) -> set[Any] | None:
    schema = resolver.resolve(schema) if isinstance(schema, Mapping) else {}
    if not isinstance(schema, Mapping):
        return None
    if "const" in schema:
        return {json.dumps(schema["const"])}
    if "enum" in schema:
        return {json.dumps(v) for v in schema["enum"]}
    for key in ("anyOf", "oneOf"):
        if key in schema:
            values: set[Any] = set()
            for part in schema[key]:
                part = resolver.resolve(part)
                if isinstance(part, Mapping) and part.get("type") == "null":
                    values.add("null")
                    continue
                inner = _enum(part, resolver)
                if inner is None:
                    return None
                values |= inner
            return values
    return None


def _object(schema: Any, resolver: _Resolver) -> dict[str, Any]:
    schema = resolver.resolve(schema) if isinstance(schema, Mapping) else {}
    if not isinstance(schema, Mapping):
        return {}
    merged: dict[str, Any] = {
        "properties": dict(schema.get("properties") or {}),
        "required": list(schema.get("required") or []),
    }
    for part in schema.get("allOf") or []:
        inner = _object(part, resolver)
        merged["properties"] = {**inner["properties"], **merged["properties"]}
        merged["required"] += inner["required"]
    for key in ("anyOf", "oneOf"):
        for part in schema.get(key) or []:
            inner = _object(part, resolver)
            if inner["properties"]:
                return inner
    return merged


def compare(
    reader: Any,
    reader_resolver: _Resolver,
    writer: Any,
    writer_resolver: _Resolver,
    *,
    every_field: bool,
    path: str = "",
    depth: int = 0,
) -> list[tuple[str, str]]:
    """What ``writer`` may send that ``reader`` cannot read, as ``(path, message)``.

    ``every_field``: the reader may use any of its fields (a published contract)
    rather than only its required ones (a consumer's model)."""
    problems: list[tuple[str, str]] = []
    if depth > MAX_DEPTH:
        return problems
    bad = _accepts(_types(reader, reader_resolver), _types(writer, writer_resolver))
    if bad:
        reader_types = sorted(_types(reader, reader_resolver) or [])
        problems.append(
            (path, f"sent as {', '.join(sorted(bad))}; expected {', '.join(reader_types)}")
        )
        return problems
    reader_enum = _enum(reader, reader_resolver)
    writer_enum = _enum(writer, writer_resolver)
    if reader_enum is not None:
        if writer_enum is None:
            problems.append((path, "no longer limited to the known values"))
        elif writer_enum - reader_enum:
            values = ", ".join(sorted(writer_enum - reader_enum))
            problems.append((path, f"new values {values}"))
    r_obj, w_obj = _object(reader, reader_resolver), _object(writer, writer_resolver)
    if not r_obj["properties"]:
        return problems
    for name, r_schema in r_obj["properties"].items():
        field_path = f"{path}.{name}" if path else name
        needed = name in r_obj["required"]
        if not needed and not every_field:
            if name in w_obj["properties"]:
                problems += compare(
                    r_schema,
                    reader_resolver,
                    w_obj["properties"][name],
                    writer_resolver,
                    every_field=every_field,
                    path=field_path,
                    depth=depth + 1,
                )
            continue
        if name not in w_obj["properties"]:
            problems.append((field_path, "removed" if every_field else "required but not sent"))
            continue
        if needed and name not in w_obj["required"]:
            problems.append((field_path, "required by the reader but optional for the writer"))
        problems += compare(
            r_schema,
            reader_resolver,
            w_obj["properties"][name],
            writer_resolver,
            every_field=every_field,
            path=field_path,
            depth=depth + 1,
        )
    return problems


# ------------------------------------------------------------------ consumer


def consumer_problems(
    router: EventRouter,
    document: Mapping[str, Any] | str | Path,
    *,
    channels: Sequence[str] = (),
    type_prefix: str = "",
) -> list[Problem]:
    """What the provider described in ``document`` may send that ``router``'s
    typed handlers cannot handle. ``channels``: the provider channels the
    consumer subscribes to (default: all of them)."""
    document = _document(document)
    provided = _messages(document, type_prefix, channels)
    resolver = _Resolver(document)
    problems: list[Problem] = []
    for event_type in router.event_types:
        candidates = [m for (_, t), m in provided.items() if t == event_type]
        if not candidates:
            problems.append(
                Problem(event_type, "", "handled here, but the provider no longer sends it")
            )
            continue
        payload_type = router.payload_type(event_type)
        if payload_type is None:
            continue
        for message in candidates:
            problems += _check_message(router, event_type, payload_type, message, resolver)
    return problems


def _check_message(
    router: EventRouter,
    event_type: str,
    payload_type: Any,
    message: Message,
    resolver: _Resolver,
) -> list[Problem]:
    problems: list[Problem] = []
    model_schema = TypeAdapter(payload_type).json_schema(mode="validation", by_alias=True)
    model_resolver = _Resolver(model_schema)
    for path, text in compare(
        model_schema, model_resolver, message.payload, resolver, every_field=False
    ):
        problems.append(Problem(event_type, path, text))
    samples = [*message.examples, _Models(resolver).sample(message.payload)]
    for index, sample in enumerate(samples):
        envelope = EventEnvelope(type=event_type, payload=sample, occurred_at=datetime.now(UTC))
        try:
            router.parse(envelope)
        except ResourceEngineError as exc:
            kind = "example" if index < len(message.examples) else "generated sample"
            fields = ", ".join(
                ".".join(str(p) for p in e.get("loc", ())) for e in (exc.details or [])
            )
            problems.append(
                Problem(event_type, "", f"the provider's {kind} does not parse ({fields})")
            )
    return problems


def assert_consumer_contract(
    router: EventRouter,
    document: Mapping[str, Any] | str | Path,
    *,
    channels: Sequence[str] = (),
    type_prefix: str = "",
) -> None:
    """``AssertionError`` listing every ``consumer_problems`` finding."""
    problems = consumer_problems(router, document, channels=channels, type_prefix=type_prefix)
    if problems:
        lines = "\n".join(f"- {p}" for p in problems)
        raise AssertionError(f"The consumer no longer matches the provider's contract:\n{lines}")


# ------------------------------------------------------------------ provider


def breaking_changes(
    old: Mapping[str, Any] | str | Path,
    new: Mapping[str, Any] | str | Path,
    *,
    type_prefix: str = "",
) -> list[Problem]:
    """Changes from ``old`` to ``new`` (both a provider's AsyncAPI documents)
    that break consumers written against ``old``. Added events, added optional
    fields and widened types are compatible and not listed."""
    old_doc, new_doc = _document(old), _document(new)
    before, after = _messages(old_doc, type_prefix), _messages(new_doc, type_prefix)
    old_resolver, new_resolver = _Resolver(old_doc), _Resolver(new_doc)
    problems: list[Problem] = []
    for key, message in before.items():
        channel, event_type = key
        current = after.get(key)
        if current is None:
            problems.append(Problem(event_type, "", f"no longer published on {channel}"))
            continue
        for path, text in compare(
            message.payload, old_resolver, current.payload, new_resolver, every_field=True
        ):
            problems.append(Problem(event_type, path, text))
    return problems


__all__ = [
    "Problem",
    "assert_consumer_contract",
    "breaking_changes",
    "compare",
    "consumer_problems",
]
