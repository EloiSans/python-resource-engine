"""What every role-aware authorizer shares: the catalogue, application roles,
system-role grants and the permission summaries behind ``/authz/me``."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from resource_engine.authz.catalog import FIELD_KINDS, Catalog, action_matches
from resource_engine.authz.config import AuthzConfig
from resource_engine.authz.roles import IdpRoleSource
from resource_engine.ports.identity import Principal

if TYPE_CHECKING:
    from resource_engine.application.registry import ResourceRegistry


class SystemPrincipal(Principal):
    """The engine itself (consumers, pipelines, jobs). Always allowed; cannot be
    produced from a token."""


def system_principal(name: str = "engine") -> SystemPrincipal:
    return SystemPrincipal(subject=name, issuer="resource-engine", provider="system", service=True)


def subject_view(principal: Principal, roles: Iterable[str]) -> dict[str, Any]:
    """The caller as policies see it (``subject.*``)."""
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


class RoleBasedAuthorizer:
    """Base of ``PolicyAuthorizer`` and ``EngineAuthorizer``.

    Subclasses implement ``check``, ``scope`` and ``field_rules``.
    """

    def __init__(self, config: AuthzConfig) -> None:
        self.config = config
        self.role_source = config.role_source or IdpRoleSource(passthrough=True)
        self.catalog: Catalog | None = None

    def bind(self, registry: ResourceRegistry) -> None:
        """Called by ``ResourceManager``: build the catalogue from the registry."""
        self.catalog = Catalog.from_registry(registry)

    async def roles_of(self, principal: Principal) -> frozenset[str]:
        roles = set(await self.role_source.roles(principal))
        if principal.provider == "apikey" and principal.issuer == "resource-engine":
            roles |= principal.roles  # an API key carries application roles, checked on create
        for provider_role in principal.roles | principal.groups:
            if provider_role in self.config.bootstrap:
                roles.add(self.config.bootstrap[provider_role])
        return frozenset(roles)

    def _wildcards(self, action: str) -> bool:
        info = self.catalog.actions.get(action) if self.catalog else None
        return info.wildcards if info else True

    def _exact(self, action: str) -> bool:
        info = self.catalog.actions.get(action) if self.catalog else None
        return info.exact if info else False

    def _system_grant(self, roles: Iterable[str], action: str) -> str | None:
        wild, exact = self._wildcards(action), self._exact(action)
        for name in sorted(roles):
            role = self.config.system_role(name)
            if role and any(
                action_matches(g, action, wildcards=wild or role.include_sensitive, exact=exact)
                for g in role.grants
            ):
                return role.name
        return None

    async def check(self, principal: Any, action: str, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def field_rules(
        self, principal: Any, resource: str, mode: str
    ) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def permissions(self, principal: Principal | None) -> dict[str, str]:
        """Every catalogue action the principal may perform: ``allowed`` or
        ``conditional`` (depends on the record or the input)."""
        out: dict[str, str] = {}
        assert self.catalog is not None
        for action, info in self.catalog.actions.items():
            if info.kind in FIELD_KINDS:
                continue
            decision = await self.check(principal, action)
            if decision.allowed is True:
                out[action] = "allowed"
            elif decision.allowed is None:
                out[action] = "conditional"
        return out

    async def field_permissions(
        self, principal: Principal | None
    ) -> dict[str, dict[str, list[str]]]:
        """Per resource: fields ``hidden`` (not readable), ``readonly`` (not
        writable) and ``conditional`` (depends on the record or input)."""
        assert self.catalog is not None
        out: dict[str, dict[str, list[str]]] = {}
        for resource in self.catalog.resource_attributes:
            read = (await self.field_rules(principal, resource, "read")).decide()
            write = (await self.field_rules(principal, resource, "write")).decide()
            entry = {
                "hidden": sorted(read.denied),
                "readonly": sorted(write.denied),
                "conditional": sorted(read.conditional | write.conditional),
            }
            if any(entry.values()):
                out[resource] = entry
        return out
