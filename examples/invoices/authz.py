"""Example policies, as a front end would create them (data, not code)."""

from __future__ import annotations

from typing import Any

APPROVER_POLICIES: list[dict[str, Any]] = [
    {
        "name": "Approvers see pending invoices",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": ["invoices:list", "invoices:get", "invoices:download:*"],
        "condition": {"in": [{"attr": "resource.status"}, ["pending", "approved"]]},
    },
    {
        "name": "Approvers decide invoices under 10,000",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": ["invoices:transition:approve", "invoices:transition:reject"],
        "condition": {"lt": [{"attr": "resource.amount"}, 10000]},
    },
]


async def seed(manager: Any, admin: Any) -> None:
    """Create the custom role and its policies (idempotent by name)."""
    if not await _role_exists(manager, admin):
        await manager.create("authz_roles", {"name": "approver"}, actor=admin)
    existing = {p.name for p in (await manager.list("authz_policies", actor=admin)).items}
    for policy in APPROVER_POLICIES:
        if policy["name"] not in existing:
            await manager.create("authz_policies", policy, actor=admin)


async def _role_exists(manager: Any, admin: Any) -> bool:
    from resource_engine.errors import EntityNotFound

    try:
        await manager.get("authz_roles", "approver", actor=admin)
    except EntityNotFound:
        return False
    return True
