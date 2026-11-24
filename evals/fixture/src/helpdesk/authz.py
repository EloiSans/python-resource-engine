"""Policies the help desk creates on start-up (data, not code)."""

from __future__ import annotations

from typing import Any

#: Custom roles created with the seed.
SEED_ROLES = ["agent"]

#: Every policy the application seeds; ``resource-engine check`` reads it.
SEED_POLICIES: list[dict[str, Any]] = [
    {
        "name": "Agents manage customers",
        "effect": "allow",
        "subjects": {"roles": ["agent"]},
        "actions": ["customers:*"],
    },
]


async def seed(manager: Any, admin: Any) -> None:
    """Create the custom roles and policies (idempotent by name)."""
    from resource_engine.errors import EntityNotFound

    for role in SEED_ROLES:
        try:
            await manager.get("authz_roles", role, actor=admin)
        except EntityNotFound:
            await manager.create("authz_roles", {"name": role}, actor=admin)
    existing = {p.name for p in (await manager.list("authz_policies", actor=admin)).items}
    for policy in SEED_POLICIES:
        if policy["name"] not in existing:
            await manager.create("authz_policies", policy, actor=admin)
