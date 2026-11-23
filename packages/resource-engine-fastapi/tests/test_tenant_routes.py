from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from resource_engine_fastapi import mount_resources


@dataclass
class Ticket:
    title: str
    tenant: str | None = None
    id: int | None = None


class TicketIn(BaseModel):
    title: str


class TicketOut(TicketIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tenant: str


class TicketResource(Resource):
    model = Ticket
    schemas = {"create": TicketIn, "read": TicketOut}
    operations = ["list", "get", "create"]
    tenant_field = "tenant"


USERS = {
    "admin": Principal(subject="admin", issuer="idp", roles=frozenset({"admins"})),
    "ana": Principal(subject="ana", issuer="idp", roles=frozenset({"staff"}), tenant="t1"),
    "bo": Principal(subject="bo", issuer="idp", roles=frozenset({"staff"}), tenant="t2"),
    "nemo": Principal(subject="nemo", issuer="idp", roles=frozenset({"staff"})),
}


async def actor(request: Request) -> Principal:
    return USERS[request.headers["authorization"].removeprefix("Bearer ")]


@pytest.fixture
async def api():
    registry = ResourceRegistry()
    registry.register(TicketResource)
    registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    authz = PolicyAuthorizer(
        AuthzConfig(role_source=IdpRoleSource({"admins": "admin", "staff": "staff"})),
        store.unit_of_work,
    )
    manager = ResourceManager(registry, store.unit_of_work, authorizer=authz)
    await manager.create("authz_roles", {"name": "staff"}, actor=USERS["admin"])
    await manager.create(
        "authz_policies",
        {
            "name": "Staff handle tickets",
            "effect": "allow",
            "subjects": {"roles": ["staff"]},
            "actions": ["tickets:*"],
        },
        actor=USERS["admin"],
    )
    app = FastAPI()
    mount_resources(app, registry, manager, actor_dependency=actor)
    transport = httpx.ASGITransport(app=app)

    def client(user: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, base_url="http://api", headers={"Authorization": f"Bearer {user}"}
        )

    return client


async def test_tenants_over_http(api):
    admin, ana, bo, nemo = api("admin"), api("ana"), api("bo"), api("nemo")
    r = await ana.post("/tickets", json={"title": "a"})
    assert r.status_code == 201 and r.json()["tenant"] == "t1"
    await bo.post("/tickets", json={"title": "b"})
    assert (await ana.get("/tickets")).json()["total"] == 1
    assert (await ana.get("/tickets/2")).status_code == 404
    r = await ana.get("/tickets", params={"tenant": "t2"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "RE-E709"
    r = await nemo.get("/tickets")
    assert r.status_code == 403 and r.json()["error"]["code"] == "RE-E708"
    assert (await admin.get("/tickets")).json()["total"] == 2
    assert (await admin.get("/tickets", params={"tenant": "t2"})).json()["total"] == 1
    r = await admin.post("/tickets", json={"title": "c"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "RE-E708"
    r = await admin.post("/tickets", params={"tenant": "t3"}, json={"title": "c"})
    assert r.status_code == 201 and r.json()["tenant"] == "t3"
