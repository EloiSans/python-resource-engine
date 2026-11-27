"""``CedarEngine``: Cedar policies evaluated in process (cedarpy)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import cedarpy
from resource_engine.ports.authz import AuthzRequest

from resource_engine_cedar.translate import residual_condition

log = logging.getLogger("resource_engine.authz.cedar")


def _pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.replace("-", "_").split("_"))


def encode(value: Any) -> Any:
    """A plain Python value as a Cedar JSON value. ``None`` has no Cedar
    equivalent: such attributes are left out (test them with ``has``)."""
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal | float):
        number = Decimal(str(value)).normalize()
        if number.as_tuple().exponent < -4:  # type: ignore[operator]
            number = number.quantize(Decimal("0.0001"))
        text = format(number, "f")
        return {"__extn": {"fn": "decimal", "arg": text if "." in text else f"{text}.0"}}
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{moment.microsecond // 1000:03d}Z"
        return {"__extn": {"fn": "datetime", "arg": text}}
    if isinstance(value, date):
        return {"__extn": {"fn": "datetime", "arg": value.isoformat()}}
    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple | set | frozenset):
        return [encode(v) for v in value if v is not None]
    return str(value)


class CedarEngine:
    """``PolicyEngine`` over Cedar policies.

    Args:
        policies: Policy text, a ``.cedar`` file, or a directory of them.
        schema: Optional Cedar schema (text, JSON, a dict or a path) to validate
            requests, such as ``cedar_schema(registry)``. With a JSON schema,
            only the attributes it declares are sent (the token's ``claims``,
            say, are left out), so requests always fit it.
        entity_types: Resource name → Cedar entity type (default: PascalCase of
            the resource name, ``invoice_lines`` → ``InvoiceLines``).
        user_type, role_type: Entity types of the principal and of its roles
            (``principal in Role::"approver"``).

    The request maps to Cedar as: principal ``User::"<subject.id>"`` with the
    subject attributes and its roles as parents; action ``Action::"<action>"``;
    resource ``<Type>::"<id>"`` with the record's attributes; context
    ``{operation, action, now, input}``.
    """

    def __init__(
        self,
        policies: str | Path,
        *,
        schema: str | Path | Mapping[str, Any] | None = None,
        entity_types: Mapping[str, str] | None = None,
        user_type: str = "User",
        role_type: str = "Role",
    ) -> None:
        self.source = policies
        if isinstance(schema, Mapping):
            schema = json.dumps(schema)
        self.schema = Path(schema).read_text() if isinstance(schema, Path) else schema
        self._declared = _declared(self.schema)
        self.entity_types = dict(entity_types or {})
        self.user_type = user_type
        self.role_type = role_type
        self.reload()

    def reload(self) -> None:
        """Parse the policies again (after editing the files)."""
        text = self.source
        if isinstance(text, Path):
            files: Iterable[Path] = sorted(text.glob("*.cedar")) if text.is_dir() else [text]
            text = "\n".join(f.read_text() for f in files)
        self.policies = cedarpy.PolicySet.from_str(text)

    def entity_type(self, resource: str) -> str:
        return self.entity_types.get(resource) or _pascal(resource)

    # ------------------------------------------------------------------ mapping

    def _principal(self, subject: Mapping[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
        if subject is None:
            uid = {"type": self.user_type, "id": "anonymous"}
            return f'{self.user_type}::"anonymous"', [{"uid": uid, "attrs": {}, "parents": []}]
        roles = list(subject.get("roles") or ())
        uid = {"type": self.user_type, "id": str(subject["id"])}
        entities = [
            {
                "uid": uid,
                "attrs": encode(self._attrs(self.user_type, subject)),
                "parents": [{"type": self.role_type, "id": r} for r in roles],
            },
            *(
                {"uid": {"type": self.role_type, "id": r}, "attrs": {}, "parents": []}
                for r in roles
            ),
        ]
        return _ref(uid), entities

    def _context(self, request: AuthzRequest) -> dict[str, Any]:
        context = {k: v for k, v in request.context.items() if v is not None}
        if request.input is not ...:
            context["input"] = request.input or {}
        if self._declared is not None:
            context = _only(context, self._declared.get(("context", request.action)))
        return encode(context)

    def _attrs(self, kind: str, values: Mapping[str, Any]) -> dict[str, Any]:
        if self._declared is None:
            return dict(values)
        return _only(values, self._declared.get(("entity", kind)))

    async def evaluate(self, request: AuthzRequest) -> Any:
        principal, entities = self._principal(request.subject)
        action = {"type": "Action", "id": request.action}
        query: dict[str, Any] = {"principal": principal, "action": _ref(action)}
        # every entity is listed: Cedar treats missing ones as unknown
        entities.append({"uid": action, "attrs": {}, "parents": []})
        resource_type = self.entity_type(request.resource_type or "resource")
        if request.resource is not ...:
            uid = {"type": resource_type, "id": str(request.entity_id)}
            query["resource"] = _ref(uid)
            attrs = self._attrs(resource_type, request.resource or {})
            entities.append({"uid": uid, "attrs": encode(attrs), "parents": []})
        if request.input is not ...:
            query["context"] = self._context(request)
        if not request.unknowns:
            result = cedarpy.is_authorized(query, self.policies, entities, self.schema)
            if result.diagnostics.errors:
                log.warning("Cedar errors for %s: %s", request.action, result.diagnostics.errors)
            return result.allowed
        partial = cedarpy.is_authorized_partial(query, self.policies, entities, self.schema)
        name = partial.decision.name if hasattr(partial.decision, "name") else partial.decision
        if name == "Allow":
            return True
        if name == "Deny":
            return False
        if not partial.residuals:  # input errors: fail closed
            log.warning("Cedar could not evaluate %s: %s", request.action, partial.diagnostics)
            return False
        return residual_condition(
            partial.residuals, resource_type=resource_type, context=request.context
        )


def _declared(schema: str | None) -> dict[tuple[str, str], Any] | None:
    """Declared attributes per entity type and per action's context, from a
    JSON schema: ``{("entity", "User"): {"id": None, ...}, ("context", a): {...}}``
    (nested records keep their own declared attributes). ``None`` for no
    schema or a schema in Cedar's text format."""
    if not schema:
        return None
    try:
        data = json.loads(schema)
    except ValueError:
        return None
    declared: dict[tuple[str, str], Any] = {}
    for namespace, body in data.items():
        prefix = f"{namespace}::" if namespace else ""
        for name, kind in body.get("entityTypes", {}).items():
            shape = kind.get("shape", {"attributes": {}})
            declared[("entity", prefix + name)] = _record(shape)
            declared[("entity", name)] = declared[("entity", prefix + name)]
        for name, action in body.get("actions", {}).items():
            context = action.get("appliesTo", {}).get("context")
            if context:
                declared[("context", name)] = _record(context)
    return declared


def _record(shape: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: _record(spec) if spec.get("type") == "Record" else None
        for name, spec in shape.get("attributes", {}).items()
    }


def _only(values: Mapping[str, Any], declared: Mapping[str, Any] | None) -> dict[str, Any]:
    """``values`` limited to the declared attributes (recursively for records)."""
    if declared is None:
        return dict(values)
    out = {}
    for name, value in values.items():
        if name not in declared:
            continue
        nested = declared[name]
        out[name] = (
            _only(value, nested) if nested is not None and isinstance(value, Mapping) else value
        )
    return out


def _ref(uid: Mapping[str, str]) -> str:
    escaped = uid["id"].replace("\\", "\\\\").replace('"', '\\"')
    return f'{uid["type"]}::"{escaped}"'
