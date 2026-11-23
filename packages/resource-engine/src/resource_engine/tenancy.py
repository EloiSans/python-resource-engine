"""Tenant scoping: confine operations on tenant-scoped resources to the caller's
tenant, independently of permission policies. See docs/tenancy.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resource_engine.errors import TenantMismatch, TenantRequired


@dataclass(frozen=True)
class TenantScope:
    """``tenant`` the operation is confined to; ``cross`` callers may act on any
    tenant (``tenant`` is then ``None`` unless they narrowed it)."""

    tenant: str | None
    cross: bool


async def is_cross_tenant(actor: Any, authorizer: Any) -> bool:
    from resource_engine.authz.evaluator import SystemPrincipal
    from resource_engine.ports.identity import Principal

    if isinstance(actor, SystemPrincipal):
        return True
    if not isinstance(actor, Principal) or authorizer is None:
        return False
    roles_of = getattr(authorizer, "roles_of", None)
    config = getattr(authorizer, "config", None)
    if roles_of is None or config is None:
        return False
    for name in await roles_of(actor):
        role = config.system_role(name)
        if role is not None and role.cross_tenant:
            return True
    return False


async def resolve_tenant(actor: Any, authorizer: Any, requested: str | None) -> TenantScope:
    """The scope of ``actor``; ``requested`` narrows cross-tenant callers and must
    match the tenant of confined ones."""
    if await is_cross_tenant(actor, authorizer):
        return TenantScope(requested, True)
    own = getattr(actor, "tenant", None)
    if not own:
        raise TenantRequired(
            "This resource is tenant-scoped and the caller has no tenant",
            hint="The identity provider must issue a tenant claim (Principal.tenant).",
            details={"reason": "no tenant"},
            doc="docs/tenancy.md",
        )
    if requested is not None and requested != own:
        raise TenantMismatch(
            f"Not allowed to act on tenant {requested!r}",
            details={"reason": "other tenant", "tenant": requested},
            doc="docs/tenancy.md",
        )
    return TenantScope(own, False)
