"""``EngineAuthorizer``: the ``Authorizer`` port on top of an external policy
engine (OPA, Cedar, OpenFGA... see the adapter packages).

The engine answers one question (``PolicyEngine.evaluate``): allowed, denied, or
a residual condition over what is not known yet (the record, the input). This
module turns those answers into everything the manager needs:

- ``check``: residuals mean "depends on the record" until the record is loaded;
- ``scope``: the residual of ``<resource>:list`` is the row filter, applied by
  the repository like the built-in policies' filters;
- ``field_rules``: fields restricted in code are asked once per operation and
  their residuals evaluated per record, locally.

System roles in code (``AuthzConfig``) still apply first: ``admin`` is allowed
without asking the engine, and drives tenancy (``cross_tenant``).
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from resource_engine.authz.base import RoleBasedAuthorizer, SystemPrincipal, subject_view
from resource_engine.authz.catalog import FIELD_KINDS, ActionInfo
from resource_engine.authz.conditions import UNKNOWN, Env, evaluate, partial
from resource_engine.authz.config import AuthzConfig
from resource_engine.ports.authz import AuthzRequest, Decision, FieldAccess, PolicyEngine
from resource_engine.ports.identity import Principal

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry


def plain(value: Any) -> Any:
    """A record or input as a dict of plain Python values (``Decimal``, ``date``
    and ``datetime`` kept; enums, sets and nested objects flattened)."""
    if value is ... or value is None:
        return value
    if isinstance(value, BaseModel):
        return {k: _plain_value(v) for k, v in value.model_dump().items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain_value(v) for k, v in value.items()}
    return {k: _plain_value(v) for k, v in vars(value).items() if not k.startswith("_")}


def _plain_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, set | frozenset | tuple | list):
        return [_plain_value(v) for v in value]
    if isinstance(value, Mapping | BaseModel):
        return plain(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(value)
    return value


class ResidualFieldRules:
    """Field decisions prepared from engine answers (see
    ``EngineAuthorizer.field_rules``): ``True``, ``False`` or a residual per
    field, evaluated per record without calling the engine again."""

    def __init__(self, rules: dict[str, Any], subject: dict[str, Any] | None = None) -> None:
        self.rules = {name: rule for name, rule in rules.items() if rule is not True}
        self.subject = subject or {}

    @property
    def empty(self) -> bool:
        return not self.rules

    def decide(self, *, resource: Any = ..., input: Any = ...) -> FieldAccess:
        denied: set[str] = set()
        conditional: set[str] = set()
        env = _env(self.subject, resource, input)
        for name, rule in self.rules.items():
            result = rule if isinstance(rule, bool) else evaluate(rule, env)
            if result is False:
                denied.add(name)
            elif result is UNKNOWN:
                conditional.add(name)
        return FieldAccess(frozenset(denied), frozenset(conditional))


def _env(subject: dict[str, Any] | None, resource: Any, input: Any) -> Env:
    namespaces: dict[str, Any] = {"subject": subject or {}}
    if resource is not ...:
        namespaces["resource"] = resource
    if input is not ...:
        namespaces["input"] = plain(input)
    return Env(**namespaces)


class EngineAuthorizer(RoleBasedAuthorizer):
    """``Authorizer`` delegating decisions to a ``PolicyEngine``.

    Args:
        engine: The adapter (``OpaEngine``, ``CedarEngine``, ``OpenFgaEngine``...).
        config: System roles, role source and bootstrap roles, as for
            ``PolicyAuthorizer``. System-role grants are decided here, before
            the engine is asked.
    """

    def __init__(self, engine: PolicyEngine, config: AuthzConfig | None = None) -> None:
        super().__init__(config or AuthzConfig())
        self.engine = engine
        self.registry: ResourceRegistry | None = None

    def bind(self, registry: ResourceRegistry) -> None:
        super().bind(registry)
        self.registry = registry
        bind = getattr(self.engine, "bind", None)
        if bind is not None:
            bind(registry)

    # ---------------------------------------------------------------- requests

    def _info(self, action: str) -> ActionInfo | None:
        return self.catalog.actions.get(action) if self.catalog else None

    def _request(
        self,
        subject: dict[str, Any] | None,
        action: str,
        *,
        resource: Any = ...,
        input: Any = ...,
        context: dict[str, Any] | None = None,
        filter: bool = False,
    ) -> AuthzRequest:
        info = self._info(action)
        resource_type = info.resource if info else action.partition(":")[0] or None
        id_field = "id"
        if self.registry is not None and resource_type and resource_type in self.registry:
            id_field = self.registry.get(resource_type).id_field
        data = plain(resource)
        return AuthzRequest(
            subject=subject,
            action=action,
            resource_type=resource_type,
            resource=data,
            input=plain(input),
            context={"action": action, "now": datetime.now(UTC), **(context or {})},
            entity_id=data.get(id_field) if isinstance(data, dict) else None,
            id_field=id_field,
            filter=filter,
        )

    async def _subject(self, principal: Any) -> tuple[dict[str, Any] | None, frozenset[str]]:
        if not isinstance(principal, Principal):
            return None, frozenset()
        roles = await self.roles_of(principal)
        return subject_view(principal, roles), roles

    def _field_grant(self, roles: frozenset[str], info: ActionInfo) -> bool:
        """A system role grants a field action; restricted fields need a grant
        naming them or a role with ``include_sensitive`` (as for policies)."""
        from resource_engine.authz.catalog import action_matches
        from resource_engine.authz.evaluator import names_field

        for name in sorted(roles):
            role = self.config.system_role(name)
            if role and any(
                action_matches(
                    g,
                    info.name,
                    wildcards=info.wildcards or role.include_sensitive,
                    exact=info.exact,
                )
                and (not info.restricted or role.include_sensitive or names_field(g))
                for g in role.grants
            ):
                return True
        return False

    # ------------------------------------------------------------------ checks

    async def check(
        self,
        principal: Principal | None,
        action: str,
        *,
        resource: Any = ...,
        input: Any = ...,
        context: dict[str, Any] | None = None,
    ) -> Decision:
        if isinstance(principal, SystemPrincipal):
            return Decision(True, "system principal")
        subject, roles = await self._subject(principal)
        info = self._info(action)
        if info is not None and info.kind in FIELD_KINDS:
            if not info.restricted or self._field_grant(roles, info):
                return Decision(True, "field allowed")
        else:
            system = self._system_grant(roles, action)
            if system:
                return Decision(True, f"system role {system}", system_role=system)
        request = self._request(subject, action, resource=resource, input=input, context=context)
        result = await self.engine.evaluate(request)
        if isinstance(result, bool):
            return Decision(result, "allowed by the engine" if result else "denied by the engine")
        local = evaluate(result, _env(subject, resource, input))
        if local is UNKNOWN:
            return Decision(None, "depends on the record")
        return Decision(local, "allowed by the engine" if local else "denied by the engine")

    async def scope(self, principal: Principal | None, action: str) -> Any:
        if isinstance(principal, SystemPrincipal):
            return True
        subject, roles = await self._subject(principal)
        if self._system_grant(roles, action):
            return True
        result = await self.engine.evaluate(self._request(subject, action, input={}, filter=True))
        if isinstance(result, bool):
            return result
        return partial(result, Env(subject=subject or {}, input={}))

    async def field_rules(
        self, principal: Principal | None, resource: str, mode: str
    ) -> ResidualFieldRules:
        """Engine answers for the fields of ``resource`` restricted in code;
        other fields are open (not asked)."""
        assert self.catalog is not None, "bind() the authorizer to a registry first"
        if isinstance(principal, SystemPrincipal):
            return ResidualFieldRules({})
        subject, roles = await self._subject(principal)
        rules: dict[str, Any] = {}
        for name, info in self.catalog.field_actions(resource, mode).items():
            if not info.restricted or self._field_grant(roles, info):
                continue
            rules[name] = await self.engine.evaluate(self._request(subject, info.name, filter=True))
        return ResidualFieldRules(rules, subject)
