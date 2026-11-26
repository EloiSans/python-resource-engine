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
    {
        "name": "Approvers see customers",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": ["customers:list", "customers:get"],
    },
    {
        "name": "Approvers see tags",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": [
            "tags:list",
            "tags:get",
            "invoice_tags:list",
            "invoice_tags:get",
            "invoice_payments:list",
            "invoice_payments:get",
        ],
    },
    {
        "name": "Approvers read internal notes",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": ["invoices:read:notes"],
    },
    {
        "name": "Amounts are frozen once submitted",
        "effect": "deny",
        "subjects": {"everyone": True},
        "actions": ["invoices:write:amount"],
        "condition": {"ne": [{"attr": "resource.status"}, "draft"]},
    },
]


ISSUER_POLICIES: list[dict[str, Any]] = [
    {
        "name": "Issuers manage invoices",
        "effect": "allow",
        "subjects": {"roles": ["issuer"]},
        "actions": [
            "invoices:list",
            "invoices:get",
            "invoices:create",
            "invoices:update",
            "invoices:delete",
            "invoices:transition:submit",
            "invoices:transition:reopen",
            "invoices:transition:pay",
            "invoices:transition:send",
            "invoices:transition:bounce",
            "invoices:transition:resend",
            "invoices:action:*",
            "invoices:attach:*",
            "invoices:detach:*",
            "invoices:download:*",
        ],
    },
    {
        "name": "Issuers manage tags",
        "effect": "allow",
        "subjects": {"roles": ["issuer"]},
        "actions": ["tags:*", "invoice_tags:*", "invoice_payments:list", "invoice_payments:get"],
    },
    {
        "name": "Issuers manage customers",
        "effect": "allow",
        "subjects": {"roles": ["issuer"]},
        "actions": [
            "customers:list",
            "customers:get",
            "customers:create",
            "customers:update",
            "customers:delete",
            "customer_balances:list",
            "customer_balances:get",
        ],
    },
]

#: Every policy the example creates; ``resource-engine check`` reads it
#: (``[tool.resource-engine] policies``) to find operations nobody can perform.
SEED_POLICIES = APPROVER_POLICIES + ISSUER_POLICIES
SEED_ROLES = ["approver", "issuer"]


async def seed(manager: Any, admin: Any) -> None:
    """Create the custom roles and their policies (idempotent by name)."""
    for role in SEED_ROLES:
        if not await _role_exists(manager, admin, role):
            await manager.create("authz_roles", {"name": role}, actor=admin)
    existing = {p.name for p in (await manager.list("authz_policies", actor=admin)).items}
    for policy in SEED_POLICIES:
        if policy["name"] not in existing:
            await manager.create("authz_policies", policy, actor=admin)


async def _role_exists(manager: Any, admin: Any, role: str) -> bool:
    from resource_engine.errors import EntityNotFound

    try:
        await manager.get("authz_roles", role, actor=admin)
    except EntityNotFound:
        return False
    return True
