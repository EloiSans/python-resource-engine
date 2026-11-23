"""REST repository against a real upstream: a Resource Engine FastAPI app in memory."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.errors import RemoteCallFailed
from resource_engine.testing import InMemoryStore
from resource_engine.testing.contracts import CONTRACT_RESOURCE, ContractItem, RepositoryContract
from resource_engine_fastapi import install_exception_handlers, mount_resources
from resource_engine_http import (
    OAuth2ClientCredentials,
    RestEndpoint,
    RestRepository,
    rest_unit_of_work_factory,
)


class ItemIn(BaseModel):
    name: str
    rank: int
    group: str = "a"


class ItemPatch(BaseModel):
    name: str | None = None
    rank: int | None = None
    group: str | None = None


class ItemOut(ItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ItemResource(Resource):
    """Items served by the upstream service."""

    name = CONTRACT_RESOURCE
    model = ContractItem
    schemas = {"create": ItemIn, "update": ItemPatch, "read": ItemOut}
    operations = ["list", "get", "create", "update", "delete"]
    filterable = ["group"]
    sortable = ["name", "rank"]


def make_registry() -> ResourceRegistry:
    registry = ResourceRegistry()
    registry.register(ItemResource)
    return registry


def upstream_app() -> FastAPI:
    registry = make_registry()
    store = InMemoryStore.from_registry(registry)
    app = FastAPI()
    install_exception_handlers(app)
    mount_resources(app, registry, ResourceManager(registry, store.unit_of_work))
    return app


ENDPOINT = RestEndpoint.for_dataclass(ContractItem, f"/{CONTRACT_RESOURCE}")


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=upstream_app())
    return httpx.AsyncClient(transport=transport, base_url="http://upstream")


class TestRestRepository(RepositoryContract):
    @pytest.fixture
    def contract_uow_factory(self, client):
        return rest_unit_of_work_factory(client, {CONTRACT_RESOURCE: ENDPOINT})

    # Remote writes are applied immediately; there is nothing to roll back.
    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_leaving_without_commit_rolls_back(self): ...

    @pytest.mark.skip(reason="RemoteUnitOfWork is not transactional by design")
    async def test_exception_rolls_back(self): ...


async def test_engine_over_rest_keeps_the_lifecycle(client):
    """A downstream engine whose resource lives in the upstream API."""
    registry = make_registry()
    manager = ResourceManager(
        registry, rest_unit_of_work_factory(client, {CONTRACT_RESOURCE: ENDPOINT})
    )
    created = await manager.create(CONTRACT_RESOURCE, {"name": "x", "rank": 5})
    assert created.id is not None
    updated = await manager.update(CONTRACT_RESOURCE, created.id, {"rank": 6})
    assert updated.rank == 6
    upstream = (await client.get(f"/{CONTRACT_RESOURCE}/{created.id}")).json()
    assert upstream["rank"] == 6


async def test_upstream_errors_become_remote_call_failed(client):
    repo = RestRepository(client, ENDPOINT)
    with pytest.raises(RemoteCallFailed) as info:
        await repo.list(ListQuery(order_by=["unknown"]))
    assert info.value.code == "RE-E501"
    assert info.value.details["status"] == 400 and info.value.details["operation"] == "list"


async def test_transport_errors_become_remote_call_failed():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://down")
    with pytest.raises(RemoteCallFailed) as info:
        await RestRepository(client, ENDPOINT).get(1)
    assert info.value.details["status"] is None


async def test_bare_list_responses_use_x_total_count():
    def handler(request):
        assert request.url.params["order_by"] == "-rank"
        body = [{"id": 1, "name": "a", "rank": 1, "extra": True}]
        return httpx.Response(200, json=body, headers={"X-Total-Count": "7"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")
    page = await RestRepository(client, ENDPOINT).list(ListQuery(order_by=["-rank"]))
    assert page.total == 7 and page.items == [ContractItem("a", 1, id=1)]


async def test_oauth2_client_credentials_caches_and_refreshes_on_401():
    tokens = iter(["t1", "t2"])
    seen: list[str] = []
    token_calls: list[dict] = []
    reject_next = {"value": False}

    def handler(request: httpx.Request):
        if request.url.path == "/oauth/token":
            assert request.headers["authorization"].startswith("Basic ")
            token_calls.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 3600})
        seen.append(request.headers["authorization"])
        if reject_next["value"]:
            reject_next["value"] = False
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    auth = OAuth2ClientCredentials("http://auth/oauth/token", "id", "secret", scope="read")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth)
    await client.get("http://api/a")
    await client.get("http://api/b")
    reject_next["value"] = True
    response = await client.get("http://api/c")

    assert response.status_code == 200
    assert seen == ["Bearer t1", "Bearer t1", "Bearer t1", "Bearer t2"]
    assert token_calls == [{"grant_type": "client_credentials", "scope": "read"}] * 2


def test_for_dataclass_omits_unassigned_id():
    assert json.dumps(ENDPOINT.to_payload(ContractItem("a", 1))) == (
        '{"name": "a", "rank": 1, "group": "a"}'
    )
