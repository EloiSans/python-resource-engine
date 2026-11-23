"""``PolicyAuthorizer``: evaluates system roles and stored ABAC policies."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from resource_engine.application.query import ListQuery
from resource_engine.authz.catalog import Catalog, action_matches
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
from resource_engine.authz.roles import IdpRoleSource
from resource_engine.ports.authz import Decision
from resource_engine.ports.identity import Principal

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry
    from resource_engine.authz.model import Policy
    from resource_engine.messaging.consumer import EventRouter

log = logging.getLogger("resource_engine.authz")


class SystemPrincipal(Principal):
    """The engine itself (consumers, pipelines, jobs). Always allowed; cannot be
    produced from a token."""


def system_principal(name: str = "engine") -> SystemPrincipal:
    return SystemPrincipal(subject=name, issuer="resource-engine", provider="system", service=True)


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


def subject_view(principal: Principal, roles: Iterable[str]) -> dict[str, Any]:
    return {
        "id": principal.id,
        "subject": principal.subject,
        "provider": principal.provider,
        "roles": sorted(roles),
        "groups": sorted(principal.groups),
        "scopes": sorted(principal.scopes),
        "tenant": principal.tenant,
        "client_id": principal.client_id,
        "service": principal.service,
        "email": principal.email,
        "name": principal.name,
        "claims": dict(principal.claims),
    }


class PolicyAuthorizer:
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
        self.config = config
        self.store = PolicyStore(uow_factory, resource=policies_resource)
        self.role_source = config.role_source or IdpRoleSource(passthrough=True)
        self.catalog: Catalog | None = None

    def bind(self, registry: ResourceRegistry) -> None:
        """Called by ``ResourceManager``: build the catalogue from the registry."""
        self.catalog = Catalog.from_registry(registry)

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

    async def roles_of(self, principal: Principal) -> frozenset[str]:
        roles = set(await self.role_source.roles(principal))
        for provider_role in principal.roles | principal.groups:
            if provider_role in self.config.bootstrap:
                roles.add(self.config.bootstrap[provider_role])
        return frozenset(roles)

    def _wildcards(self, action: str) -> bool:
        info = self.catalog.actions.get(action) if self.catalog else None
        return info.wildcards if info else True

    def _system_grant(self, roles: Iterable[str], action: str) -> str | None:
        wild = self._wildcards(action)
        for name in sorted(roles):
            role = self.config.system_role(name)
            if role and any(
                action_matches(g, action, wildcards=wild or role.include_sensitive)
                for g in role.grants
            ):
                return role.name
        return None

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
        wild = self._wildcards(action)
        return [
            p
            for p in await self.store.policies()
            if self._applies(p, principal, roles)
            and any(action_matches(a, action, wildcards=wild) for a in p.actions)
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
        pending_deny: list[str] = []
        for p in (p for p in policies if p.effect == "deny"):
            result = evaluate(p.condition, env)
            if result is True:
                return Decision(False, f"denied by policy {p.name!r}", (p.id,))
            if result is UNKNOWN:
                pending_deny.append(p.id)
        unknown_allow: list[str] = []
        for p in (p for p in policies if p.effect == "allow"):
            result = evaluate(p.condition, env)
            if result is True:
                if pending_deny:
                    return Decision(None, "depends on the record", (p.id, *pending_deny))
                return Decision(True, f"allowed by policy {p.name!r}", (p.id,))
            if result is UNKNOWN:
                unknown_allow.append(p.id)
        if unknown_allow:
            return Decision(None, "depends on the record", tuple(unknown_allow))
        return Decision(False, "no policy allows it")

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

    async def permissions(self, principal: Principal | None) -> dict[str, str]:
        """Every catalogue action the principal may perform: ``allowed`` or
        ``conditional`` (depends on the record or the input)."""
        out: dict[str, str] = {}
        assert self.catalog is not None
        for action in self.catalog.actions:
            decision = await self.check(principal, action)
            if decision.allowed is True:
                out[action] = "allowed"
            elif decision.allowed is None:
                out[action] = "conditional"
        return out


def _plain(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else value
