"""``PolicyAuthorizer``: evaluates system roles and stored ABAC policies."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from resource_engine.application.query import ListQuery
from resource_engine.authz.base import (  # noqa: F401  (re-exported)
    RoleBasedAuthorizer,
    SystemPrincipal,
    subject_view,
    system_principal,
)
from resource_engine.authz.catalog import FIELD_KINDS, ActionInfo, action_matches
from resource_engine.authz.conditions import (
    UNKNOWN,
    Env,
    conjunction,
    disjunction,
    evaluate,
    negation,
    partial,
)
from resource_engine.authz.config import AuthzConfig
from resource_engine.ports.authz import Decision, FieldAccess
from resource_engine.ports.identity import Principal

if TYPE_CHECKING:
    from resource_engine.authz.model import Policy
    from resource_engine.messaging.consumer import EventRouter

log = logging.getLogger("resource_engine.authz")


class PolicyStore:
    """Snapshot of enabled policies, reloaded after changes or every ``ttl`` seconds."""

    def __init__(
        self, uow_factory: Callable[[], Any], *, resource: str = "authz_policies", ttl: float = 60
    ) -> None:
        self.uow_factory = uow_factory
        self.resource = resource
        self.ttl = ttl
        self._policies: list[Policy] | None = None
        self._loaded = 0.0

    def invalidate(self) -> None:
        self._policies = None

    async def policies(self) -> list[Policy]:
        if self._policies is None or time.monotonic() - self._loaded > self.ttl:
            async with self.uow_factory() as uow:
                page = await uow.repository(self.resource).list(ListQuery(limit=100_000))
                # plain copies: ORMs expire instances when the unit of work closes
                self._policies = [_copy(p) for p in page.items if p.enabled]
            self._loaded = time.monotonic()
        return self._policies


def _copy(policy: Any) -> Any:
    import dataclasses

    return dataclasses.replace(policy)


class PolicyAuthorizer(RoleBasedAuthorizer):
    """``Authorizer`` over system roles (code) and policies (data).

    Rules: deny by default; a matching system-role grant allows and cannot be
    denied; otherwise any applicable deny policy whose condition holds denies, and
    an applicable allow policy whose condition holds allows.

    Args:
        config: System roles, role source and bootstrap roles.
        uow_factory: To read policies (and stored role assignments).
    """

    def __init__(
        self,
        config: AuthzConfig,
        uow_factory: Callable[[], Any],
        *,
        policies_resource: str = "authz_policies",
    ) -> None:
        super().__init__(config)
        self.store = PolicyStore(uow_factory, resource=policies_resource)
        self._restricted_cache: tuple[int, frozenset[str]] | None = None

    def invalidate(self) -> None:
        self.store.invalidate()
        invalidate = getattr(self.role_source, "invalidate", None)
        if invalidate:
            invalidate()

    def register(self, router: EventRouter) -> EventRouter:
        """Refresh caches when another instance changes policies or assignments."""
        router.register("PolicyChanged", lambda envelope: self.invalidate())
        return router

    # ------------------------------------------------------------------ roles

    @staticmethod
    def _applies(policy: Policy, principal: Principal, roles: frozenset[str]) -> bool:
        s = policy.subjects or {}
        return bool(
            s.get("everyone")
            or roles & set(s.get("roles") or ())
            or principal.groups & set(s.get("groups") or ())
            or principal.id in (s.get("users") or ())
        )

    async def _applicable(
        self, principal: Principal, roles: frozenset[str], action: str
    ) -> list[Policy]:
        wild, exact = self._wildcards(action), self._exact(action)
        return [
            p
            for p in await self.store.policies()
            if self._applies(p, principal, roles)
            and any(action_matches(a, action, wildcards=wild, exact=exact) for a in p.actions)
        ]

    # ----------------------------------------------------------------- checks

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
        if not isinstance(principal, Principal):
            return Decision(False, "not authenticated")
        info = self.catalog.actions.get(action) if self.catalog else None
        if info is not None and info.kind in FIELD_KINDS:
            return await self._check_field(principal, info, resource=resource, input=input)
        roles = await self.roles_of(principal)
        system = self._system_grant(roles, action)
        if system:
            return Decision(True, f"system role {system}", system_role=system)
        namespaces: dict[str, Any] = {
            "subject": subject_view(principal, roles),
            "context": {"action": action, "now": datetime.now(UTC), **(context or {})},
        }
        if resource is not ...:
            namespaces["resource"] = resource
        if input is not ...:
            namespaces["input"] = _plain(input)
        env = Env(**namespaces)
        policies = await self._applicable(principal, roles, action)
        allowed, reason, ids = _decide(policies, env, open_=False)
        return Decision(allowed, reason, ids)

    async def _check_field(
        self, principal: Principal, info: ActionInfo, *, resource: Any, input: Any
    ) -> Decision:
        roles = await self.roles_of(principal)
        policies = await self.store.policies()
        entry = self._field_entry(principal, roles, info, policies, self._restricted(policies))
        if entry is None:
            return Decision(True, "field allowed")
        rules = PolicyFieldRules(
            {info.qualifier or info.name: entry}, subject_view(principal, roles)
        )
        _, ok_policies, restricted = entry
        env = rules.env(info, resource=resource, input=input)
        allowed, reason, ids = _decide(ok_policies, env, open_=not restricted)
        return Decision(allowed, reason, ids)

    async def scope(self, principal: Principal | None, action: str) -> Any:
        if isinstance(principal, SystemPrincipal):
            return True
        if not isinstance(principal, Principal):
            return False
        roles = await self.roles_of(principal)
        if self._system_grant(roles, action):
            return True
        env = Env(
            subject=subject_view(principal, roles),
            context={"action": action, "now": datetime.now(UTC)},
            input={},
        )
        policies = await self._applicable(principal, roles, action)
        allows = [partial(p.condition, env) for p in policies if p.effect == "allow"]
        denies = [partial(p.condition, env) for p in policies if p.effect == "deny"]
        return conjunction(disjunction(*allows), negation(disjunction(*denies)))

    # ----------------------------------------------------------------- fields

    def _restricted(self, policies: list[Policy]) -> frozenset[str]:
        """Field actions named explicitly by an enabled allow policy."""
        if self._restricted_cache and self._restricted_cache[0] == id(policies):
            return self._restricted_cache[1]
        assert self.catalog is not None
        fields = [a for a in self.catalog.actions.values() if a.kind in FIELD_KINDS]
        found: set[str] = set()
        for policy in policies:
            if policy.effect != "allow":
                continue
            for pattern in policy.actions:
                if not names_field(pattern):
                    continue
                found.update(
                    a.name for a in fields if action_matches(pattern, a.name, wildcards=a.wildcards)
                )
        result = frozenset(found)
        self._restricted_cache = (id(policies), result)
        return result

    async def field_rules(
        self, principal: Principal | None, resource: str, mode: str
    ) -> PolicyFieldRules:
        """Field decisions of ``principal`` on ``resource`` for ``mode`` (``read`` or
        ``write``), prepared once and evaluated per record with ``decide``.

        A field is open unless it is *restricted* (``restricted_fields`` in code,
        or named explicitly by an allow policy): open fields are allowed unless a
        deny applies, restricted ones need a system-role grant or an allow.
        """
        assert self.catalog is not None, "bind() the authorizer to a registry first"
        if isinstance(principal, SystemPrincipal):
            return PolicyFieldRules({}, {})
        actions = self.catalog.field_actions(resource, mode)
        policies = await self.store.policies()
        restricted = self._restricted(policies)
        known = isinstance(principal, Principal)
        roles = await self.roles_of(principal) if known else frozenset()
        rules: dict[str, tuple[ActionInfo, list[Policy], bool]] = {}
        for name, info in actions.items():
            entry = self._field_entry(principal, roles, info, policies, restricted)
            if entry is not None:
                rules[name] = entry
        subject = subject_view(principal, roles) if known else {}
        return PolicyFieldRules(rules, subject)

    def _field_entry(
        self,
        principal: Principal | None,
        roles: frozenset[str],
        info: ActionInfo,
        policies: list[Policy],
        restricted: frozenset[str],
    ) -> tuple[ActionInfo, list[Policy], bool] | None:
        """Policies that decide one field action, or ``None`` when it is allowed
        outright (system role) or open with no policy about it.

        Restricted fields are only granted by patterns that name them explicitly:
        broad grants (``invoices:*``) do not reach them, like sensitive resources.
        """
        is_restricted = info.restricted or info.name in restricted
        known = isinstance(principal, Principal)
        if known:
            for role_name in sorted(roles):
                role = self.config.system_role(role_name)
                if role and any(
                    action_matches(
                        g,
                        info.name,
                        wildcards=info.wildcards or role.include_sensitive,
                        exact=info.exact,
                    )
                    and (not is_restricted or role.include_sensitive or names_field(g))
                    for g in role.grants
                ):
                    return None
        applicable: list[Policy] = []
        if known:
            for p in policies:
                if not self._applies(p, principal, roles):
                    continue
                hits = [
                    a
                    for a in p.actions
                    if action_matches(a, info.name, wildcards=info.wildcards, exact=info.exact)
                ]
                if p.effect == "allow" and is_restricted:
                    hits = [a for a in hits if names_field(a)]
                if hits:
                    applicable.append(p)
        if applicable or is_restricted:
            return info, applicable, is_restricted
        return None


class PolicyFieldRules:
    """Prepared field decisions (see ``PolicyAuthorizer.field_rules``)."""

    def __init__(
        self,
        rules: dict[str, tuple[ActionInfo, list[Policy], bool]],
        subject: dict[str, Any],
    ) -> None:
        self.rules = rules
        self.subject = subject

    @property
    def empty(self) -> bool:
        """No field can be blocked: callers can skip per-record evaluation."""
        return not self.rules

    def env(self, info: ActionInfo, *, resource: Any = ..., input: Any = ...) -> Env:
        namespaces: dict[str, Any] = {
            "subject": self.subject,
            "context": {"action": info.name, "now": datetime.now(UTC)},
        }
        if resource is not ...:
            namespaces["resource"] = resource
        if input is not ...:
            namespaces["input"] = _plain(input)
        return Env(**namespaces)

    def decide(self, *, resource: Any = ..., input: Any = ...) -> FieldAccess:
        if not self.rules:
            return FieldAccess()
        denied: set[str] = set()
        conditional: set[str] = set()
        for name, (info, policies, restricted) in self.rules.items():
            env = self.env(info, resource=resource, input=input)
            allowed, _, _ = _decide(policies, env, open_=not restricted)
            if allowed is False:
                denied.add(name)
            elif allowed is None:
                conditional.add(name)
        return FieldAccess(frozenset(denied), frozenset(conditional))


def names_field(pattern: str) -> bool:
    """Whether ``pattern`` names a field explicitly (``res:read:salary``,
    ``res:*:salary``), as restricted fields require."""
    parts = pattern.split(":")
    return len(parts) == 3 and parts[2] != "*" and parts[1] in (*FIELD_KINDS, "*")


def _decide(
    policies: Iterable[Policy], env: Env, *, open_: bool
) -> tuple[bool | None, str, tuple[str, ...]]:
    """Deny overrides allow. ``open_``: allowed when no deny applies (open fields);
    otherwise an allow is needed (actions, restricted fields)."""
    policies = list(policies)
    pending_deny: list[str] = []
    for p in (p for p in policies if p.effect == "deny"):
        result = evaluate(p.condition, env)
        if result is True:
            return False, f"denied by policy {p.name!r}", (p.id,)
        if result is UNKNOWN:
            pending_deny.append(p.id)
    if open_:
        if pending_deny:
            return None, "depends on the record", tuple(pending_deny)
        return True, "open field", ()
    unknown_allow: list[str] = []
    for p in (p for p in policies if p.effect == "allow"):
        result = evaluate(p.condition, env)
        if result is True:
            if pending_deny:
                return None, "depends on the record", (p.id, *pending_deny)
            return True, f"allowed by policy {p.name!r}", (p.id,)
        if result is UNKNOWN:
            unknown_allow.append(p.id)
    if unknown_allow:
        return None, "depends on the record", tuple(unknown_allow)
    return False, "no policy allows it", ()


def _plain(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value
