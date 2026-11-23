"""Built-in resources the front end uses to manage permissions.

Register them with ``registry.register_authz()``. They are ordinary resources (CRUD,
events, docs) whose hooks enforce the rules of permission management. Their
actions are *sensitive*: wildcards in policies do not match them, only system
roles with ``include_sensitive`` (``admin``) or explicit grants.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from resource_engine.application.context import OperationContext
from resource_engine.application.resource import Resource
from resource_engine.authz.conditions import validate
from resource_engine.authz.model import (
    AssignmentCreate,
    AssignmentRead,
    Policy,
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    Role,
    RoleAssignment,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)
from resource_engine.domain.events import DomainEvent
from resource_engine.errors import InvalidPolicy, PrivilegeEscalation


@dataclass(frozen=True, kw_only=True)
class PolicyChanged(DomainEvent):
    """Policies or role assignments changed: caches must be refreshed."""

    resource: str
    entity_id: Any


def _actor_id(ctx: OperationContext) -> str | None:
    return getattr(ctx.actor, "id", None) if ctx.actor is not None else None


def _authz(ctx: OperationContext) -> Any:
    return ctx.service("authz")


async def _require_held(ctx: OperationContext, actions: list[str]) -> None:
    """No privilege escalation: the editor must hold each action unconditionally."""
    authz = _authz(ctx)
    missing = [a for a in actions if (await authz.check(ctx.actor, a)).allowed is not True]
    if missing:
        raise PrivilegeEscalation(
            f"You cannot grant actions you do not hold: {', '.join(missing[:5])}",
            details={"actions": missing},
        )


async def _granted(authz: Any, patterns: list[str]) -> set[str]:
    """Actions an allow policy effectively grants: restricted fields only through
    patterns that name them."""
    from resource_engine.authz.catalog import FIELD_KINDS
    from resource_engine.authz.evaluator import names_field

    restricted = authz._restricted(await authz.store.policies())
    out: set[str] = set()
    for pattern in patterns:
        for name in authz.catalog.matching(pattern):
            info = authz.catalog.actions[name]
            is_restricted = info.kind in FIELD_KINDS and (info.restricted or name in restricted)
            if not is_restricted or names_field(pattern):
                out.add(name)
    return out


async def validate_policy(ctx: OperationContext) -> None:
    """Checks actions, subjects and the condition against the catalogue, and that
    the editor does not grant more than they hold."""
    authz = _authz(ctx)
    catalog = authz.catalog
    entity: Policy = ctx.entity
    problems: list[dict[str, str]] = []
    matched: list[str] = []
    for i, pattern in enumerate(entity.actions):
        found = catalog.matching(pattern)
        if not found:
            problems.append(
                {"path": f"$.actions[{i}]", "message": f"{pattern!r} matches no action"}
            )
        matched += found
    system = {r.name for r in authz.config.system_roles}
    for i, role in enumerate((entity.subjects or {}).get("roles") or []):
        if role in system:
            problems.append(
                {
                    "path": f"$.subjects.roles[{i}]",
                    "message": f"{role!r} is a system role; its permissions are fixed in code",
                }
            )
    if entity.condition is not None and matched:
        types = catalog.attribute_types(matched)
        problems += [p.to_dict() for p in validate(entity.condition, types, path="$.condition")]
    if problems:
        raise InvalidPolicy(
            f"Policy {entity.name!r} is not valid",
            hint="See details.problems; GET /authz/catalog lists actions and attributes.",
            details={"problems": problems},
        )
    if entity.effect == "allow":
        await _require_held(ctx, sorted(await _granted(authz, entity.actions)))


def stamp_created(ctx: OperationContext) -> None:
    """Records who created the policy and when."""
    ctx.entity.created_at = datetime.now(UTC)
    ctx.entity.created_by = _actor_id(ctx)


def stamp_updated(ctx: OperationContext) -> None:
    """Records who changed the policy last and when."""
    ctx.entity.updated_at = datetime.now(UTC)
    ctx.entity.updated_by = _actor_id(ctx)


def record_change(ctx: OperationContext) -> None:
    """Emits ``PolicyChanged`` so every instance refreshes its permission caches."""
    ctx.record_event(PolicyChanged(resource=ctx.resource.name, entity_id=ctx.entity_id))


def refresh_caches(ctx: OperationContext) -> None:
    """Refreshes this instance's policy and role caches after the commit."""
    _authz(ctx).invalidate()


class PolicyResource(Resource):
    """Access policies (ABAC), managed from the front end.

    A policy allows or denies actions to subjects (roles, groups, users or everyone)
    when its condition holds. System roles are fixed in code and cannot be
    targeted or restricted here.

    Business area:
        Permissions
    """

    name = "authz_policies"
    model = Policy
    id_type = str
    schemas = {"create": PolicyCreate, "update": PolicyUpdate, "read": PolicyRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Lists the access policies.",
        "get": "Returns an access policy.",
        "create": "Creates an access policy (validated against the catalogue).",
        "update": "Changes an access policy.",
        "delete": "Deletes an access policy.",
    }
    filterable = ["effect", "enabled", "name"]
    sortable = ["name", "created_at", "updated_at"]
    authz_wildcards = False
    hooks = {
        "before_create": [stamp_created, validate_policy],
        "before_update": [stamp_updated],
        "after_update": [validate_policy],
        "after_create": [record_change],
        "after_delete": [record_change],
        "after_commit": [refresh_caches],
    }

    @classmethod
    def build_entity(cls, data: Any, ctx: OperationContext) -> Policy:
        values = data.model_dump()
        return Policy(id=uuid.uuid4().hex, **values)

    @classmethod
    def apply_update(cls, entity: Any, data: Any, ctx: OperationContext) -> None:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(entity, key, value)
        record_change(ctx)


async def validate_role(ctx: OperationContext) -> None:
    """Custom roles cannot reuse system role names."""
    if _authz(ctx).config.system_role(ctx.entity.name):
        raise InvalidPolicy(
            f"{ctx.entity.name!r} is a system role",
            details={"problems": [{"path": "$.name", "message": "reserved by a system role"}]},
        )
    ctx.entity.created_at = datetime.now(UTC)


class RoleResource(Resource):
    """Custom roles, created from the front end. System roles are defined in code.

    Business area:
        Permissions
    """

    name = "authz_roles"
    model = Role
    id_field = "name"
    id_type = str
    schemas = {"create": RoleCreate, "update": RoleUpdate, "read": RoleRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {
        "list": "Lists the custom roles.",
        "get": "Returns a custom role.",
        "create": "Creates a custom role.",
        "update": "Changes a custom role's description.",
        "delete": "Deletes a custom role.",
    }
    sortable = ["name"]
    authz_wildcards = False
    hooks = {"before_create": [validate_role]}


async def validate_assignment(ctx: OperationContext) -> None:
    """Exactly one of principal or group; the role exists; the editor holds every
    action the role grants."""
    authz = _authz(ctx)
    a: RoleAssignment = ctx.entity
    if (a.principal is None) == (a.group is None):
        raise InvalidPolicy(
            "Assign the role to exactly one principal or group",
            details={"problems": [{"path": "$", "message": "set principal or group"}]},
        )
    system = authz.config.system_role(a.role)
    if system is not None:
        actions: list[str] = []
        for grant in system.grants:
            actions += authz.catalog.matching(grant, include_sensitive=system.include_sensitive)
    else:
        if await ctx.repository("authz_roles").get(a.role) is None:
            raise InvalidPolicy(
                f"Unknown role {a.role!r}",
                details={"problems": [{"path": "$.role", "message": "no such role"}]},
            )
        actions = []
        for policy in await authz.store.policies():
            if policy.effect == "allow" and a.role in (policy.subjects or {}).get("roles", []):
                for pattern in policy.actions:
                    actions += authz.catalog.matching(pattern)
    await _require_held(ctx, sorted(set(actions)))
    a.created_at = datetime.now(UTC)
    a.created_by = _actor_id(ctx)


class RoleAssignmentResource(Resource):
    """Roles given to principals or groups from the front end (used by
    ``StoredRoleSource``).

    Business area:
        Permissions
    """

    name = "authz_role_assignments"
    model = RoleAssignment
    id_type = str
    schemas = {"create": AssignmentCreate, "read": AssignmentRead}
    operations = ["list", "get", "create", "delete"]
    operation_docs = {
        "list": "Lists role assignments, filterable by role, principal or group.",
        "get": "Returns a role assignment.",
        "create": "Gives a role to a principal or a group.",
        "delete": "Removes a role assignment.",
    }
    filterable = ["role", "principal", "group"]
    authz_wildcards = False
    hooks = {
        "before_create": [validate_assignment],
        "after_create": [record_change],
        "after_delete": [record_change],
        "after_commit": [refresh_caches],
    }

    @classmethod
    def build_entity(cls, data: Any, ctx: OperationContext) -> RoleAssignment:
        return RoleAssignment(id=uuid.uuid4().hex, **data.model_dump())
