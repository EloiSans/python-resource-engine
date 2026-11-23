"""Permissions: system roles in code, ABAC policies as data managed from front ends.

See docs/permissions.md.
"""

from resource_engine.authz.base import RoleBasedAuthorizer, subject_view
from resource_engine.authz.catalog import ActionInfo, AttributeInfo, Catalog, action_matches
from resource_engine.authz.conditions import UNKNOWN, Env, evaluate, partial, validate
from resource_engine.authz.config import AuthzConfig, SystemRole
from resource_engine.authz.evaluator import (
    PolicyAuthorizer,
    PolicyFieldRules,
    PolicyStore,
    SystemPrincipal,
    system_principal,
)
from resource_engine.authz.external import EngineAuthorizer, ResidualFieldRules, plain
from resource_engine.authz.fields import hidden_fields, mask
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
    "EngineAuthorizer",
    "Env",
    "IdpRoleSource",
    "Policy",
    "PolicyAuthorizer",
    "PolicyChanged",
    "PolicyFieldRules",
    "PolicyResource",
    "PolicyStore",
    "ResidualFieldRules",
    "Role",
    "RoleAssignment",
    "RoleAssignmentResource",
    "RoleBasedAuthorizer",
    "RoleResource",
    "RoleSource",
    "StoredRoleSource",
    "SystemPrincipal",
    "SystemRole",
    "action_matches",
    "evaluate",
    "hidden_fields",
    "mask",
    "partial",
    "plain",
    "subject_view",
    "system_principal",
    "validate",
]
