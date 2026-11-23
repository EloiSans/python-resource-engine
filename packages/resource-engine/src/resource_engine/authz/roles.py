"""Where a principal's application roles come from (a deployment choice)."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from resource_engine.application.query import ListQuery
from resource_engine.ports.identity import Principal


class RoleSource(Protocol):
    async def roles(self, principal: Principal) -> frozenset[str]: ...


class IdpRoleSource:
    """Roles from the token, mapped to application role names.

    Args:
        mapping: Identity-provider role or group → application role. With
            ``passthrough=True``, unmapped provider roles are used as they are.
    """

    def __init__(
        self, mapping: Mapping[str, str] | None = None, *, passthrough: bool = False
    ) -> None:
        self.mapping = dict(mapping or {})
        self.passthrough = passthrough

    async def roles(self, principal: Principal) -> frozenset[str]:
        out = set()
        for name in principal.roles | principal.groups:
            if name in self.mapping:
                out.add(self.mapping[name])
            elif self.passthrough:
                out.add(name)
        return frozenset(out)


class StoredRoleSource:
    """Roles assigned from the front end (the ``authz_role_assignments`` resource),
    to the principal (``Principal.id``) or to one of its groups. Cached briefly."""

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        resource: str = "authz_role_assignments",
        ttl: float = 30.0,
    ) -> None:
        self.uow_factory = uow_factory
        self.resource = resource
        self.ttl = ttl
        self._cache: dict[str, tuple[float, frozenset[str]]] = {}

    def invalidate(self) -> None:
        self._cache.clear()

    async def roles(self, principal: Principal) -> frozenset[str]:
        cached = self._cache.get(principal.id)
        if cached and time.monotonic() - cached[0] < self.ttl:
            return cached[1]
        roles: set[str] = set()
        async with self.uow_factory() as uow:
            repo = uow.repository(self.resource)
            page = await repo.list(ListQuery(filters={"principal": principal.id}, limit=1000))
            roles |= {a.role for a in page.items}
            for group in principal.groups:
                page = await repo.list(ListQuery(filters={"group": group}, limit=1000))
                roles |= {a.role for a in page.items}
        result = frozenset(roles)
        self._cache[principal.id] = (time.monotonic(), result)
        return result


class CombinedRoleSource:
    def __init__(self, *sources: RoleSource) -> None:
        self.sources = sources

    def invalidate(self) -> None:
        for source in self.sources:
            if hasattr(source, "invalidate"):
                source.invalidate()

    async def roles(self, principal: Principal) -> frozenset[str]:
        out: set[str] = set()
        for source in self.sources:
            out |= await source.roles(principal)
        return frozenset(out)
