"""Permissions: system roles in code, ABAC policies as data managed from front ends.

See docs/permissions.md.
"""

from resource_engine.authz.catalog import ActionInfo, AttributeInfo, Catalog, action_matches
from resource_engine.authz.conditions import UNKNOWN, Env, evaluate, partial, validate
from resource_engine.authz.config import AuthzConfig, SystemRole
from resource_engine.authz.evaluator import (
    PolicyAuthorizer,
    PolicyStore,
    SystemPrincipal,
    system_principal,
)
from resource_engine.authz.model import Policy, Role, RoleAssignment
from resource_engine.authz.resources import (
    PolicyChanged,
    PolicyResource,
    RoleAssignmentResource,
    RoleResource,
)
from resource_engine.authz.roles import (
    CombinedRoleSource,
    IdpRoleSource,
    RoleSource,
    StoredRoleSource,
)

__all__ = [
    "UNKNOWN",
    "ActionInfo",
    "AttributeInfo",
    "AuthzConfig",
    "Catalog",
    "CombinedRoleSource",
    "Env",
    "IdpRoleSource",
    "Policy",
    "PolicyAuthorizer",
    "PolicyChanged",
    "PolicyResource",
    "PolicyStore",
    "Role",
    "RoleAssignment",
    "RoleAssignmentResource",
    "RoleResource",
    "RoleSource",
    "StoredRoleSource",
    "SystemPrincipal",
    "SystemRole",
    "action_matches",
    "evaluate",
    "partial",
    "system_principal",
    "validate",
]
