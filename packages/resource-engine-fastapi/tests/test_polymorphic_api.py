"""A base resource with subtypes through every generated surface: REST, GraphQL,
MCP tools and the generated docs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore
from resource_engine_fastapi import mount_resources
from statemachine import State, StateMachine


class RequestFlow(StateMachine):
    """Request flow.

    States:
        open: Open.
        granted: Granted.

    Transitions:
        grant: Grant it.
    """

    open = State(initial=True)
    granted = State(final=True)
    grant = open.to(granted)


@dataclass
class Request:
    title: str
    type: str | None = None
    status: str | None = None
    id: int | None = None


@dataclass
class Vacation(Request):
    days: int = 0


@dataclass
class Expense(Request):
    amount: Decimal = Decimal(0)


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    type: str
    status: str


class VacationIn(BaseModel):
    title: str
    days: int


class VacationRead(RequestRead):
    days: int


class ExpenseIn(BaseModel):
    title: str
    amount: Decimal


class ExpenseRead(RequestRead):
    amount: Decimal


class RequestResource(Resource):
    """Every request."""

    name = "requests"
    model = Request
    schemas = {"read": RequestRead}
    operations = ["list", "get"]
    discriminator = "type"
    state_machine = RequestFlow


class VacationResource(Resource):
    """Vacation requests."""

    name = "vacation_requests"
    extends = RequestResource
    identity = "vacation"
    model = Vacation
    schemas = {"create": VacationIn, "read": VacationRead}
    operations = ["get", "create"]


class ExpenseResource(Resource):
    """Expense claims."""

    name = "expense_requests"
    extends = RequestResource
    identity = "expense"
    model = Expense
    schemas = {"create": ExpenseIn, "read": ExpenseRead}
    operations = ["create"]


@pytest.fixture
def manager() -> ResourceManager:
    registry = ResourceRegistry()
    registry.register(VacationResource)
    registry.register(ExpenseResource)
    return ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)


def test_rest(manager):
    app = FastAPI()
    mount_resources(app, manager.registry, manager)
    with TestClient(app) as client:
        assert client.post("/vacation_requests", json={"title": "S", "days": 5}).status_code == 201
        client.post("/expense_requests", json={"title": "Taxi", "amount": "9.90"})
        page = client.get("/requests").json()
        assert [(r["type"], r.get("days"), r.get("amount")) for r in page["items"]] == [
            ("vacation", 5, None),
            ("expense", None, "9.90"),
        ]
        assert client.get("/requests", params={"type": "expense"}).json()["total"] == 1
        granted = client.post("/requests/1/transitions/grant").json()
        assert (granted["status"], granted["days"]) == ("granted", 5)
        assert client.post("/requests", json={"title": "x"}).status_code == 405
        assert client.get("/vacation_requests/2").status_code == 404
        spec = client.get("/openapi.json").json()
    get_base = spec["paths"]["/requests/{entity_id}"]["get"]
    schema = get_base["responses"]["200"]["content"]["application/json"]["schema"]
    refs = {s["$ref"].rsplit("/", 1)[-1] for s in schema["anyOf"]}
    assert refs == {"VacationRead", "ExpenseRead"}


async def test_graphql(manager):
    from resource_engine_graphql import GraphQLAdapter

    adapter = GraphQLAdapter(manager.registry, manager)
    sdl = adapter.sdl()
    assert "interface Request {" in sdl
    assert "type VacationRequest implements Request {" in sdl
    assert "type ExpenseRequest implements Request {" in sdl  # no queries, still a type
    await manager.create("vacation_requests", {"title": "S", "days": 5})
    await manager.create("expense_requests", {"title": "Taxi", "amount": "9.90"})
    result = await adapter.execute(
        """{ requests { items { __typename id title allowedTransitions
               ... on VacationRequest { days } ... on ExpenseRequest { amount } } } }"""
    )
    assert "errors" not in result, result
    assert result["data"]["requests"]["items"] == [
        {
            "__typename": "VacationRequest",
            "id": 1,
            "title": "S",
            "allowedTransitions": ["grant"],
            "days": 5,
        },
        {
            "__typename": "ExpenseRequest",
            "id": 2,
            "title": "Taxi",
            "allowedTransitions": ["grant"],
            "amount": "9.90",
        },
    ]
    granted = await adapter.execute("mutation { grantRequest(id: 2) { __typename status } }")
    assert granted["data"]["grantRequest"] == {"__typename": "ExpenseRequest", "status": "granted"}


def test_mcp_and_docs(manager):
    from resource_engine_devtools.documentation import introspect
    from resource_engine_devtools.documentation.generators import render_markdown
    from resource_engine_mcp import build_tools

    tools = build_tools(manager.registry, manager)
    assert {"requests_list", "requests_get", "requests_transition_grant"} <= set(tools)
    assert "vacation_requests_transition_grant" not in tools
    pages = render_markdown(introspect(manager.registry))
    base = next(
        v
        for k, v in pages.items()
        if k.endswith("requests.md") and "vacation" not in k and "expense" not in k
    )
    assert "`vacation` ([vacation_requests](vacation_requests.md))" in base
    sub = next(v for k, v in pages.items() if k.endswith("vacation_requests.md"))
    assert "Subtipus de [requests](requests.md) (`vacation`)" in sub
