"""A Cedar schema generated from the registry, to validate policies against
the application's real resources, actions and attributes.

```python
from resource_engine_cedar import cedar_schema, validate

schema = cedar_schema(registry)
problems = validate(Path("policies/"), schema)   # [] when every policy fits
```

``resource-engine authz cedar-schema`` writes it, and ``--check policies/``
validates in CI. The schema follows what ``CedarEngine`` sends:

- **Principal:** ``User`` (``id``, ``subject``, ``provider``, ``roles``,
  ``groups``, ``scopes`` and the optional ``tenant``, ``client_id``,
  ``service``, ``email``, ``name``), member of ``Role``. The token's other
  ``claims`` are left out: their shape is the provider's, so policies use
  ``has`` on them and are validated without them.
- **Resources:** one entity type per resource (``invoices`` → ``Invoices``,
  or ``entity_types``), with the entity's attributes and their types.
- **Actions:** every action of the catalogue, applying to its resource type.
  Its context has ``action``, ``now`` and ``operation``, and ``input`` typed
  from the operation's input schema when there is one.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import types
import typing
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from resource_engine_cedar.engine import _pascal

STRING = {"type": "String"}
LONG = {"type": "Long"}
BOOLEAN = {"type": "Boolean"}
DECIMAL = {"type": "Extension", "name": "decimal"}
DATETIME = {"type": "Extension", "name": "datetime"}


def cedar_type(annotation: Any) -> dict[str, Any] | None:
    """The Cedar type of a Python annotation; ``None`` when it has none."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Annotated:
        return cedar_type(args[0])
    if origin in (typing.Union, types.UnionType):
        options = [a for a in args if a is not type(None)]
        return cedar_type(options[0]) if len(options) == 1 else None
    if origin is typing.Literal:
        kinds = {type(a) for a in args}
        return STRING if kinds == {str} else LONG if kinds == {int} else None
    if origin in (list, set, frozenset, tuple, typing.Sequence) or annotation in (list, set):
        element = cedar_type(args[0]) if args else None
        return {"type": "Set", "element": element} if element else None
    if annotation is bool:
        return BOOLEAN
    if annotation is int:
        return LONG
    if annotation is str:
        return STRING
    if annotation in (Decimal, float):
        return DECIMAL
    if annotation in (dt.datetime, dt.date):
        return DATETIME
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        values = {type(m.value) for m in annotation}
        return STRING if values == {str} else LONG if values == {int} else None
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
        return {"type": "Record", "attributes": _model_attributes(annotation)}
    return None


def _optional(annotation: Any) -> bool:
    origin = typing.get_origin(annotation)
    return origin in (typing.Union, types.UnionType) and type(None) in typing.get_args(annotation)


def _attribute(annotation: Any, *, required: bool) -> dict[str, Any] | None:
    kind = cedar_type(annotation)
    if kind is None:
        return None
    return {**kind, "required": False} if not required or _optional(annotation) else kind


def _model_attributes(model: type) -> dict[str, Any]:
    out = {}
    for name, info in model.model_fields.items():  # type: ignore[attr-defined]
        attribute = _attribute(info.annotation, required=info.is_required())
        if attribute is not None:
            out[name] = attribute
    return out


def _entity_attributes(model: type) -> dict[str, Any]:
    """Attributes of a domain entity (dataclass or Pydantic). Fields typed
    ``X | None`` are optional: ``None`` values are left out of the request, so
    policies test them with ``has``."""
    if hasattr(model, "model_fields"):
        hints = {n: f.annotation for n, f in model.model_fields.items()}  # type: ignore[attr-defined]
    elif dataclasses.is_dataclass(model):
        resolved = typing.get_type_hints(model)
        hints = {f.name: resolved.get(f.name, f.type) for f in dataclasses.fields(model)}
    else:
        hints = typing.get_type_hints(model)
    out = {}
    for name, annotation in hints.items():
        if name.startswith("_"):
            continue
        attribute = _attribute(annotation, required=True)
        if attribute is not None:
            out[name] = attribute
    return out


def _principal(user_type: str, role_type: str) -> dict[str, Any]:
    optional = {"required": False}
    attributes = {
        "id": STRING,
        "subject": STRING,
        "provider": STRING,
        "roles": {"type": "Set", "element": STRING},
        "groups": {"type": "Set", "element": STRING},
        "scopes": {"type": "Set", "element": STRING},
        "tenant": {**STRING, **optional},
        "client_id": {**STRING, **optional},
        "service": {**BOOLEAN, **optional},
        "email": {**STRING, **optional},
        "name": {**STRING, **optional},
    }
    return {"memberOfTypes": [role_type], "shape": {"type": "Record", "attributes": attributes}}


def cedar_schema(
    registry: Any,
    *,
    entity_types: Mapping[str, str] | None = None,
    user_type: str = "User",
    role_type: str = "Role",
    namespace: str = "",
) -> dict[str, Any]:
    """The Cedar schema (JSON format) of the application's resources and actions."""
    from resource_engine.authz.catalog import Catalog

    names = dict(entity_types or {})

    def entity(resource: str) -> str:
        return names.get(resource) or _pascal(resource)

    types_: dict[str, Any] = {
        user_type: _principal(user_type, role_type),
        role_type: {},
    }
    inputs: dict[str, Any] = {}
    for definition in registry:
        types_[entity(definition.name)] = {
            "shape": {"type": "Record", "attributes": _entity_attributes(definition.model)}
        }
        for op in ("create", "update"):
            if op in definition.schemas and definition.enabled(op):
                inputs[f"{definition.name}:{op}"] = definition.schemas[op]
        for event, schema in definition.transition_schemas.items():
            inputs[f"{definition.name}:transition:{event}"] = schema
        for action in definition.actions.values():
            if action.input_schema is not None:
                inputs[f"{definition.name}:action:{action.name}"] = action.input_schema

    actions: dict[str, Any] = {}
    for name, info in Catalog.from_registry(registry).actions.items():
        context: dict[str, Any] = {
            "action": STRING,
            "now": DATETIME,
            "operation": {**STRING, "required": False},
        }
        if name in inputs:
            context["input"] = {
                "type": "Record",
                "attributes": _model_attributes(inputs[name]),
                "required": False,
            }
        actions[name] = {
            "appliesTo": {
                "principalTypes": [user_type],
                "resourceTypes": [entity(info.resource)],
                "context": {"type": "Record", "attributes": context},
            }
        }
    return {namespace: {"entityTypes": types_, "actions": actions}}


def validate(policies: str | Path, schema: Mapping[str, Any] | str) -> list[str]:
    """Problems of ``policies`` (text, a ``.cedar`` file or a directory) against
    ``schema``: unknown actions, entity types or attributes, wrong types. Empty
    when every policy fits."""
    import cedarpy

    text = policies
    if isinstance(text, Path):
        files = sorted(text.glob("*.cedar")) if text.is_dir() else [text]
        text = "\n".join(f.read_text() for f in files)
    schema_text = schema if isinstance(schema, str) else json.dumps(schema)
    result = cedarpy.validate_policies(text, schema_text)
    if result.validation_passed:
        return []
    return [str(error) for error in result.errors]


__all__ = ["cedar_schema", "cedar_type", "validate"]
