"""Routes a permission-management front end needs besides the CRUD of the
``authz_*`` resources (which ``mount_resources`` generates like any other)."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from resource_engine import ResourceManager
from resource_engine.errors import EntityNotFound, PermissionDenied
from resource_engine.ports.identity import Principal


class ExplainAs(BaseModel):
    """A hypothetical caller, described as its identity provider would."""

    subject: str = "explain"
    roles: list[str] = Field(default_factory=list, description="Provider roles")
    groups: list[str] = Field(default_factory=list)
    tenant: str | None = None


class ExplainRequest(BaseModel):
    action: str = Field(description="Full action name, e.g. invoices:transition:approve")
    entity_id: Any = Field(default=None, description="Record to evaluate against")
    as_principal: ExplainAs | None = Field(
        default=None, alias="as", description="Evaluate for this caller instead of yourself"
    )


def build_authz_router(manager: ResourceManager, *, actor_dependency: Any) -> APIRouter:
    authz = manager.authorizer
    router = APIRouter(prefix="/authz", tags=["authz"])

    async def require_reader(actor: Any) -> None:
        if (await authz.check(actor, "authz_policies:list")).allowed is not True:
            raise PermissionDenied(
                "Not allowed: authz_policies:list",
                details={"action": "authz_policies:list"},
            )

    @router.get("/catalog", summary="Actions, attributes and operators for policy editors")
    async def catalog(actor: Any = Depends(actor_dependency)) -> dict[str, Any]:
        await require_reader(actor)
        data = authz.catalog.to_dict()
        data["system_roles"] = [
            {
                "name": r.name,
                "grants": list(r.grants),
                "description": r.description,
                "system": True,
            }
            for r in authz.config.system_roles
        ]
        return data

    @router.get("/me", summary="The caller's roles and permitted actions")
    async def me(actor: Any = Depends(actor_dependency)) -> dict[str, Any]:
        if not isinstance(actor, Principal):
            return {"principal": None, "roles": [], "actions": {}}
        return {
            "principal": actor.id,
            "roles": sorted(await authz.roles_of(actor)),
            "actions": await authz.permissions(actor),
        }

    @router.post("/explain", summary="Why an action is allowed or denied")
    async def explain(
        request: ExplainRequest, actor: Any = Depends(actor_dependency)
    ) -> dict[str, Any]:
        await require_reader(actor)
        target = actor
        if request.as_principal is not None:
            a = request.as_principal
            target = Principal(
                subject=a.subject,
                issuer="explain",
                roles=frozenset(a.roles),
                groups=frozenset(a.groups),
                tenant=a.tenant,
            )
        resource: Any = ...
        if request.entity_id is not None:
            name = request.action.split(":", 1)[0]
            async with manager.uow_factory() as uow:
                resource = await uow.repository(name).get(request.entity_id)
                if resource is None:
                    raise EntityNotFound(f"{name} {request.entity_id!r} does not exist")
                decision = await authz.check(target, request.action, resource=resource)
        else:
            decision = await authz.check(target, request.action)
        roles = sorted(await authz.roles_of(target)) if isinstance(target, Principal) else []
        return {"action": request.action, "roles": roles, **decision.to_dict()}

    return router
