"""Code-side configuration: system roles and where roles come from."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from resource_engine.errors import ConfigurationError

if TYPE_CHECKING:
    from resource_engine.authz.roles import RoleSource


@dataclass(frozen=True)
class SystemRole:
    """A role defined in code: visible and assignable from the front end, never
    editable there, and not restrictable by front-end deny policies.

    Args:
        name: Role name.
        grants: Action patterns (``*``, ``*:list``, ``invoices:*``...).
        description: Shown to administrators.
        include_sensitive: Wildcards also match resources with
            ``authz_wildcards = False`` (only for full administrators).
    """

    name: str
    grants: Sequence[str]
    description: str | None = None
    include_sensitive: bool = False

    @classmethod
    def admin(cls, name: str = "admin") -> SystemRole:
        return cls(name, ["*"], "Full access, including permission management", True)

    @classmethod
    def viewer(cls, name: str = "viewer") -> SystemRole:
        return cls(name, ["*:list", "*:get", "*:download:*"], "Read-only access")


@dataclass(frozen=True)
class AuthzConfig:
    """Args:
    system_roles: Roles defined in code.
    role_source: Where principals' roles come from (deployment choice):
        ``IdpRoleSource``, ``StoredRoleSource`` or ``CombinedRoleSource``.
    bootstrap: Identity-provider roles that always grant a role (e.g.
        ``{"platform-admin": "admin"}``), so administrators cannot lock
        themselves out.
    """

    system_roles: Sequence[SystemRole] = (SystemRole.admin(), SystemRole.viewer())
    role_source: RoleSource | None = None
    bootstrap: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [r.name for r in self.system_roles]
        if len(names) != len(set(names)):
            raise ConfigurationError(f"Duplicate system role names: {names}")
        for target in self.bootstrap.values():
            if target not in names:
                raise ConfigurationError(f"bootstrap grants unknown system role {target!r}")

    def system_role(self, name: str) -> SystemRole | None:
        return next((r for r in self.system_roles if r.name == name), None)
