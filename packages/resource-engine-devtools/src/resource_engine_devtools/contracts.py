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
  changed type, new enum values. Each event version (``schema_version``) is
  compared with the same version; a new version may change anything, and a
  version may stop being published once its sunset has passed.
  ``resource-engine docs asyncapi --check FILE`` runs it in CI.
- **REST API:** ``openapi_breaking_changes(old, new)`` does the same for the
  OpenAPI document: removed operations (unless deprecated and past their
  sunset), response fields removed or changed, request fields newly required.

See docs/contracts.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
) -> dict[tuple[str, str, str], Message]:
    """The provider's messages by ``(channel, event type, schema version)``."""
    try:
        found = provider_messages(
            document, type_prefix=type_prefix, channels=channels, unique=False
        )
    except SystemExit:  # no messages at all
        return {}
    return {(m.channel, m.event_type, m.schema_version): m for m in found}


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
        wanted = router.schema_version(event_type)  # None: every version
        candidates = [
            m
            for (_, t, version), m in provided.items()
            if t == event_type and (wanted is None or version == wanted)
        ]
        if not candidates:
            problems.append(
                Problem(
                    event_type,
                    "",
                    "handled here, but the provider no longer sends it"
                    + (f" (version {wanted})" if wanted else ""),
                )
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
    today = datetime.now(UTC).date()
    for key, message in before.items():
        channel, event_type, version = key
        current = after.get(key)
        label = event_type if version == "1" else f"{event_type} v{version}"
        if current is None:
            sunset = (message.deprecation or {}).get("sunset")
            if sunset and date.fromisoformat(sunset) <= today:
                continue  # retired after its announced sunset
            problems.append(
                Problem(
                    label,
                    "",
                    f"no longer published on {channel}"
                    + (f" (its sunset is {sunset})" if sunset else ""),
                )
            )
            continue
        for path, text in compare(
            message.payload, old_resolver, current.payload, new_resolver, every_field=True
        ):
            problems.append(Problem(label, path, text))
    return problems


_METHODS = ("get", "put", "post", "patch", "delete")


def _json_schema(node: Mapping[str, Any] | None, resolver: _Resolver) -> Any:
    content = (resolver.resolve(node or {}).get("content") or {}) if node else {}
    for media, body in content.items():
        if "json" in media:
            return body.get("schema")
    return None


def _success(operation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    responses = operation.get("responses") or {}
    for code in sorted(responses):
        if str(code).startswith("2"):
            return responses[code]
    return None


def openapi_breaking_changes(
    old: Mapping[str, Any] | str | Path, new: Mapping[str, Any] | str | Path
) -> list[Problem]:
    """Changes from ``old`` to ``new`` (OpenAPI documents of the same API) that
    break clients written against ``old``: a removed operation (unless it was
    deprecated and its ``x-deprecation`` sunset has passed), response fields
    removed or changed, request fields or parameters newly required. Each
    problem's ``event_type`` is the operation (``GET /v1/invoices``)."""

    def load(document: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
        if isinstance(document, Mapping):
            return document
        return json.loads(Path(document).read_text(encoding="utf-8"))

    old_doc, new_doc = load(old), load(new)
    old_resolver, new_resolver = _Resolver(old_doc), _Resolver(new_doc)
    today = datetime.now(UTC).date()
    problems: list[Problem] = []
    for path, old_item in (old_doc.get("paths") or {}).items():
        new_item = (new_doc.get("paths") or {}).get(path) or {}
        for method in _METHODS:
            before = old_item.get(method)
            if before is None:
                continue
            label = f"{method.upper()} {path}"
            after = new_item.get(method)
            if after is None:
                sunset = (before.get("x-deprecation") or {}).get("sunset")
                if before.get("deprecated") and sunset and date.fromisoformat(sunset) <= today:
                    continue
                problems.append(
                    Problem(
                        label,
                        "",
                        "removed" + (f" before its sunset ({sunset})" if sunset else ""),
                    )
                )
                continue
            old_params = {
                (p.get("in"), p.get("name"))
                for p in (old_resolver.resolve(x) for x in before.get("parameters") or [])
            }
            for param in (new_resolver.resolve(x) for x in after.get("parameters") or []):
                if param.get("required") and (param.get("in"), param.get("name")) not in old_params:
                    problems.append(Problem(label, param.get("name", ""), "new required parameter"))
            old_out = _json_schema(_success(before), old_resolver)
            new_out = _json_schema(_success(after), new_resolver)
            if old_out is not None and new_out is not None:
                for where, text in compare(
                    old_out, old_resolver, new_out, new_resolver, every_field=True
                ):
                    problems.append(
                        Problem(label, f"response.{where}" if where else "response", text)
                    )
            old_in = _json_schema(before.get("requestBody"), old_resolver)
            new_in = _json_schema(after.get("requestBody"), new_resolver)
            if old_in is not None and new_in is not None:
                for where, text in compare(
                    new_in, new_resolver, old_in, old_resolver, every_field=False
                ):
                    if text == "required by the reader but optional for the writer":
                        text = "now required"
                    problems.append(
                        Problem(label, f"request.{where}" if where else "request", text)
                    )
    return problems


__all__ = [
    "Problem",
    "assert_consumer_contract",
    "breaking_changes",
    "compare",
    "consumer_problems",
    "openapi_breaking_changes",
]
