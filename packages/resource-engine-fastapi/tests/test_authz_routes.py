from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastapi import Request
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, SystemRole
from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from resource_engine_fastapi import mount_resources


@dataclass
class Note:
    text: str
    owner: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str
    owner: str


class NoteOut(NoteIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["list", "get", "create"]


@dataclass
class Profile:
    name: str
    phone: str
    id: int | None = None


class ProfileIn(BaseModel):
    name: str
    phone: str


class ProfileOut(ProfileIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProfileResource(Resource):
    model = Profile
    schemas = {"create": ProfileIn, "read": ProfileOut}
    operations = ["list", "get", "create"]
    restricted_fields = ["phone"]


USERS = {
    "admin": Principal(subject="admin", issuer="idp", roles=frozenset({"admins"})),
    "viewer": Principal(subject="vera", issuer="idp", roles=frozenset({"viewers"})),
    "writer": Principal(subject="wil", issuer="idp", roles=frozenset({"writers"})),
}


async def actor(request: Request) -> Principal:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if token not in USERS:
        raise AuthenticationFailed("unknown", reason="invalid_token")
    return USERS[token]


@pytest.fixture
def api():
    registry = ResourceRegistry()
    registry.register(NoteResource)
    registry.register(ProfileResource)
    registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        system_roles=[SystemRole.admin(), SystemRole.viewer()],
        role_source=IdpRoleSource({"admins": "admin", "viewers": "viewer", "writers": "writer"}),
    )
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=PolicyAuthorizer(config, store.unit_of_work)
    )
    from fastapi import FastAPI

    app = FastAPI()
    mount_resources(app, registry, manager, actor_dependency=actor)
    transport = httpx.ASGITransport(app=app)

    def client(user: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, base_url="http://api", headers={"Authorization": f"Bearer {user}"}
        )

    return client


async def test_front_end_manages_policies(api):
    admin, viewer, writer = api("admin"), api("viewer"), api("writer")
    catalog = (await admin.get("/authz/catalog")).json()
    assert "notes:create" in [a["name"] for a in catalog["resources"]["notes"]["actions"]]
    assert {r["name"] for r in catalog["system_roles"]} == {"admin", "viewer"}
    assert (await viewer.get("/authz/catalog")).status_code == 403

    r = await writer.post("/notes", json={"text": "hi", "owner": "wil"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "RE-E702"

    policy = {
        "name": "Writers write their own notes",
        "effect": "allow",
        "subjects": {"roles": ["writer"]},
        "actions": ["notes:create", "notes:list", "notes:get"],
        "condition": {"eq": [{"attr": "resource.owner"}, {"attr": "subject.subject"}]},
    }
    r = await admin.post("/authz_policies", json=policy)
    assert r.status_code == 201, r.text
    assert (await writer.post("/notes", json={"text": "hi", "owner": "wil"})).status_code == 201
    assert (await writer.post("/notes", json={"text": "x", "owner": "bob"})).status_code == 403
    await admin.post("/notes", json={"text": "admin's", "owner": "admin"})
    listed = (await writer.get("/notes")).json()
    assert listed["total"] == 1 and listed["items"][0]["owner"] == "wil"
    assert (await writer.get("/notes/2")).status_code == 404

    me = (await writer.get("/authz/me")).json()
    assert me["roles"] == ["writer"] and me["actions"]["notes:create"] == "conditional"
    explained = (
        await admin.post(
            "/authz/explain",
            json={
                "action": "notes:get",
                "entity_id": 1,
                "as": {"subject": "wil", "roles": ["writers"]},
            },
        )
    ).json()
    assert explained["allowed"] is True and explained["policies"]

    bad = {**policy, "condition": {"eq": [{"attr": "resource.ownr"}, 1]}}
    r = await admin.post("/authz_policies", json=bad)
    assert r.status_code == 422 and r.json()["error"]["details"]["problems"][0]["path"]
    assert (await viewer.post("/authz_policies", json=policy)).status_code == 403


async def test_hidden_fields_are_omitted_over_http(api):
    admin, viewer, writer = api("admin"), api("viewer"), api("writer")
    r = await admin.post("/profiles", json={"name": "Ada", "phone": "555"})
    assert r.status_code == 201 and r.json()["phone"] == "555"
    assert (await viewer.get("/profiles/1")).json() == {"id": 1, "name": "Ada"}
    listed = (await viewer.get("/profiles")).json()
    assert listed["total"] == 1 and listed["items"] == [{"id": 1, "name": "Ada"}]
    r = await viewer.get("/profiles", params={"order_by": "phone"})
    assert r.status_code in (400, 403)  # phone is not sortable at all here

    await admin.post(
        "/authz_policies",
        json={
            "name": "Writers create profiles",
            "effect": "allow",
            "subjects": {"roles": ["writer"]},
            "actions": ["profiles:*"],
        },
    )
    r = await writer.post("/profiles", json={"name": "Bo", "phone": "1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "RE-E706"
    assert r.json()["error"]["details"]["fields"] == ["phone"]
    me = (await writer.get("/authz/me")).json()
    assert me["fields"]["profiles"] == {
        "hidden": ["phone"],
        "readonly": ["phone"],
        "conditional": [],
    }
    assert "profiles:read:name" not in me["actions"]
