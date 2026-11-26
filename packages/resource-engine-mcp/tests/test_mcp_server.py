"""The MCP server on the invoices example: tools, calls, permissions, transports."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from invoices.authz import seed
from invoices.bootstrap import authz, registry
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import AuthenticationFailed, ConfigurationError
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_mcp import ResourceMcpServer, build_tools

ADMIN = Principal(subject="root", issuer="https://sso", roles=frozenset({"platform-admin"}))
ACCOUNTANT = Principal(subject="ada", issuer="https://sso", roles=frozenset({"accountant"}))
APPROVER = Principal(subject="pau", issuer="https://sso", roles=frozenset({"approver"}))


class _Silent:
    async def send(self, message: str) -> None:
        pass


def _manager(**kw) -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    return ResourceManager(
        registry,
        store.unit_of_work,
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        **kw,
    )


@pytest.fixture
def manager() -> ResourceManager:
    return _manager(audit=True)


@pytest.fixture
async def secured() -> ResourceManager:
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
        services={"notifier": _Silent()},
        audit=True,
    )
    await seed(manager, ADMIN)
    return manager


def _names(tools) -> set[str]:
    return {t.name for t in tools}


# ------------------------------------------------------------------ tools


def test_tools_from_the_registry(manager):
    tools = build_tools(registry, manager, resources=["invoices"])
    assert set(tools) == {
        "invoices_list",
        "invoices_get",
        "invoices_create",
        "invoices_update",
        "invoices_delete",
        "invoices_allowed_transitions",
        "invoices_history",
        "invoices_transition_submit",
        "invoices_transition_approve",
        "invoices_transition_reject",
        "invoices_transition_pay",
        "invoices_transition_reopen",
        "invoices_transition_send",
        "invoices_transition_bounce",
        "invoices_transition_resend",
        "invoices_action_recalculate_totals",
        "invoices_action_render_summary",
        "invoices_tags",
        "invoices_payments",
    }
    create = tools["invoices_create"].to_mcp()
    assert create.input_schema["required"] == ["data"]
    assert "customer" in create.input_schema["properties"]["data"]["properties"]
    assert "idempotency_key" in create.input_schema["properties"]
    assert create.description.startswith("Crea una factura")
    assert create.annotations.read_only_hint is False

    listing = tools["invoices_list"].to_mcp()
    assert listing.annotations.read_only_hint is True
    assert set(listing.input_schema["properties"]["filters"]["properties"]) >= {"status"}

    delete = tools["invoices_delete"].to_mcp()
    assert delete.annotations.destructive_hint is True
    assert "expected_version" in delete.input_schema["properties"]

    reject = tools["invoices_transition_reject"].to_mcp()
    assert reject.input_schema["required"] == ["id", "data"]
    assert "pending → rejected" in reject.description
    # fields may be hidden by field permissions: outputs require none of them
    assert "required" not in tools["invoices_get"].output_schema


def test_include_and_exclude(manager):
    tools = build_tools(registry, manager, include=["invoices_*"], exclude=["*_delete"])
    assert "invoices_delete" not in tools
    assert all(name.startswith("invoices_") for name in tools)


def test_history_needs_a_stored_audit_trail():
    assert "invoices_history" not in build_tools(registry, _manager())


def test_duplicate_tool_names_are_refused(manager, monkeypatch):
    from resource_engine_mcp import tools as module

    original = module._Builder.build

    def doubled(self):
        built = original(self)
        return built + built[:1]

    monkeypatch.setattr(module._Builder, "build", doubled)
    with pytest.raises(ConfigurationError, match="Two MCP tools"):
        build_tools(registry, manager)


# ------------------------------------------------------------------ calls


async def test_calls_go_through_the_manager(manager):
    server = ResourceMcpServer(registry, manager)
    async with Client(server.server) as client:
        created = await client.call_tool(
            "invoices_create", {"data": {"customer": "ACME", "amount": 10}}
        )
        assert not created.is_error
        invoice = created.structured_content
        assert invoice["status"] == "draft"

        updated = await client.call_tool(
            "invoices_update",
            {"id": invoice["id"], "data": {"amount": 12}, "expected_version": invoice["version"]},
        )
        assert updated.structured_content["amount"] == "12"

        allowed = await client.call_tool("invoices_allowed_transitions", {"id": invoice["id"]})
        assert allowed.structured_content == {"transitions": ["submit"]}

        await client.call_tool("invoices_transition_submit", {"id": invoice["id"]})
        rejected = await client.call_tool(
            "invoices_transition_reject",
            {"id": invoice["id"], "data": {"reason": "Falta el NIF"}},
        )
        assert rejected.structured_content["status"] == "rejected"

        history = await client.call_tool(
            "invoices_history", {"id": invoice["id"], "machine": "approval"}
        )
        states = [c["to_state"] for c in history.structured_content["changes"]]
        assert states == ["draft", "pending", "rejected"]

        page = await client.call_tool(
            "invoices_list", {"filters": {"status": "rejected"}, "limit": 10}
        )
        assert page.structured_content["total"] == 1
        assert page.structured_content["items"][0]["id"] == invoice["id"]

        refused = await client.call_tool("invoices_delete", {"id": invoice["id"]})
        assert refused.is_error  # a domain rule: rejected invoices are kept
        draft = await client.call_tool("invoices_create", {"data": {"customer": "B", "amount": 1}})
        draft_id = draft.structured_content["id"]
        deleted = await client.call_tool("invoices_delete", {"id": str(draft_id)})
        assert deleted.structured_content == {"id": draft_id, "deleted": True}


async def test_errors_are_tool_results(manager):
    server = ResourceMcpServer(registry, manager)
    async with Client(server.server) as client:
        invalid = await client.call_tool("invoices_create", {"data": {"amount": 1}})
        assert invalid.is_error
        assert invalid.structured_content["error"]["code"] == "RE-E101"

        missing = await client.call_tool("invoices_get", {"id": 999})
        assert missing.structured_content["error"]["code"] == "RE-E102"

        created = await client.call_tool(
            "invoices_create", {"data": {"customer": "ACME", "amount": 1}}
        )
        invoice = created.structured_content
        stale = await client.call_tool(
            "invoices_update", {"id": invoice["id"], "data": {"amount": 2}, "expected_version": 9}
        )
        assert stale.structured_content["error"]["code"] == "RE-E105"

        early = await client.call_tool("invoices_transition_pay", {"id": invoice["id"]})
        assert early.structured_content["error"]["code"] == "RE-E201"

        unknown = await client.call_tool("invoices_explode", {})
        assert unknown.is_error

        extra = await server.call(server.tools["invoices_get"], {"id": invoice["id"], "x": 1})
        assert extra.is_error
        assert extra.structured_content["error"]["code"] == "RE-E101"


async def test_idempotency_key_replays_the_first_result(manager):
    server = ResourceMcpServer(registry, manager)
    args = {"data": {"customer": "ACME", "amount": 10}, "idempotency_key": "k-1"}
    first = await server.call(server.tools["invoices_create"], args)
    again = await server.call(server.tools["invoices_create"], args)
    assert again.structured_content == first.structured_content
    assert (await manager.list("invoices")).total == 1


# ------------------------------------------------------------ permissions


async def test_tools_listed_per_caller(secured):
    accountant = ResourceMcpServer(registry, secured, actor=ACCOUNTANT, resources=["invoices"])
    async with Client(accountant.server) as client:
        names = _names((await client.list_tools()).tools)
    assert {"invoices_list", "invoices_get", "invoices_history"} <= names
    assert "invoices_create" not in names
    assert "invoices_transition_approve" not in names

    approver = ResourceMcpServer(registry, secured, actor=APPROVER, resources=["invoices"])
    async with Client(approver.server) as client:
        names = _names((await client.list_tools()).tools)
    assert "invoices_transition_approve" in names

    unfiltered = ResourceMcpServer(
        registry, secured, actor=ACCOUNTANT, resources=["invoices"], filter_tools=False
    )
    async with Client(unfiltered.server) as client:
        assert "invoices_create" in _names((await client.list_tools()).tools)
        denied = await client.call_tool(
            "invoices_create", {"data": {"customer": "ACME", "amount": 1}}
        )
    assert denied.structured_content["error"]["code"] == "RE-E702"


async def test_hidden_fields_are_left_out(secured):
    invoice = await secured.create(
        "invoices", {"customer": "ACME", "amount": 10, "notes": "call first"}, actor=ADMIN
    )
    server = ResourceMcpServer(registry, secured, actor=ACCOUNTANT)
    async with Client(server.server) as client:
        await client.list_tools()  # the client validates outputs against these schemas
        result = await client.call_tool("invoices_get", {"id": invoice.id})
    assert not result.is_error
    assert "notes" not in result.structured_content
    assert result.structured_content["customer"] == "ACME"


async def test_resolve_actor(secured):
    server = ResourceMcpServer(
        registry, secured, resources=["invoices"], resolve_actor=lambda ctx: APPROVER
    )
    async with Client(server.server) as client:
        assert "invoices_transition_approve" in _names((await client.list_tools()).tools)


async def test_calls_are_audited_as_the_caller(secured):
    server = ResourceMcpServer(registry, secured, actor=ADMIN)
    result = await server.call(
        server.tools["invoices_create"], {"data": {"customer": "ACME", "amount": 3}}
    )
    entity_id = str(result.structured_content["id"])
    entries = await secured.list("audit_log", actor=ADMIN)
    [created] = [
        e
        for e in entries.items
        if e.resource == "invoices" and e.operation == "create" and str(e.entity_id) == entity_id
    ]
    assert created.actor == "oidc:root"
    assert created.outcome == "ok"


# -------------------------------------------------------------- transports


class FakeAuthenticator:
    async def authenticate(self, token: str) -> Principal:
        principals = {"accountant": ACCOUNTANT, "approver": APPROVER}
        if token not in principals:
            raise AuthenticationFailed("bad token", reason="invalid_token")
        return principals[token]


def _http_client(app, token: str | None = None) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000", headers=headers
    )


async def test_http_with_bearer_tokens(secured):
    app = FastAPI()
    server = ResourceMcpServer(registry, secured, resources=["invoices"])
    server.mount(
        app,
        authenticator=FakeAuthenticator(),
        resource_url="http://127.0.0.1:8000/mcp",
        issuer_url="https://sso.example.com/realms/acme",
        stateless_http=True,
        json_response=True,
    )
    async with app.router.lifespan_context(app):
        async with _http_client(app) as http:
            refused = await http.post("/mcp", json={})
            assert refused.status_code == 401
            assert "resource_metadata" in refused.headers["www-authenticate"]
            metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.json()["authorization_servers"] == [
                "https://sso.example.com/realms/acme"
            ]

        for token, expected in (("accountant", False), ("approver", True)):
            async with (
                _http_client(app, token) as http,
                Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)) as c,
            ):
                names = _names((await c.list_tools()).tools)
                assert ("invoices_transition_approve" in names) is expected
                assert "invoices_list" in names


async def test_stdio():
    script = Path(__file__).parent / "stdio_server.py"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(script)],
        env={"PYTHONPATH": str(Path(__file__).parents[3] / "examples")},
    )
    async with Client(params) as client:
        created = await client.call_tool(
            "invoices_create", {"data": {"customer": "ACME", "amount": 5}}
        )
        assert created.structured_content["customer"] == "ACME"
        assert "invoices_list" in _names((await client.list_tools()).tools)


async def test_expand_and_related_filters(manager):
    server = ResourceMcpServer(registry, manager)
    get = server.tools["invoices_get"].to_mcp()
    assert get.input_schema["properties"]["expand"]["items"]["pattern"].startswith(
        "^(customer_record|payments(:[0-9]+)?|tags"
    )
    filters = server.tools["invoices_list"].to_mcp().input_schema["properties"]["filters"]
    assert "customer_record.country" in filters["properties"]
    async with Client(server.server) as client:
        await client.list_tools()  # outputs are validated against these schemas
        customer = await client.call_tool(
            "customers_create", {"data": {"name": "Globex", "tax_id": "B22", "country": "FR"}}
        )
        cid = customer.structured_content["id"]
        created = await client.call_tool(
            "invoices_create", {"data": {"customer": "x", "amount": 1, "customer_id": cid}}
        )
        iid = created.structured_content["id"]
        one = await client.call_tool("invoices_get", {"id": iid, "expand": ["customer_record"]})
        assert not one.is_error
        assert one.structured_content["customer_record"]["name"] == "Globex"
        page = await client.call_tool(
            "invoices_list", {"filters": {"customer_record.country": "FR"}}
        )
        assert [i["id"] for i in page.structured_content["items"]] == [iid]
        missing = await client.call_tool(
            "invoices_create", {"data": {"customer": "x", "amount": 1, "customer_id": 404}}
        )
        assert missing.structured_content["error"]["code"] == "RE-E108"


async def test_reverse_relations(manager):
    server = ResourceMcpServer(registry, manager)
    tool = server.tools["customers_invoices"].to_mcp()
    assert tool.annotations.read_only_hint is True
    assert "id" in tool.input_schema["required"]
    pattern = server.tools["customers_get"].to_mcp().input_schema["properties"]["expand"]
    assert pattern["items"]["pattern"].startswith("^(invoices(:[0-9]+)?|balance(:[0-9]+)?)")
    async with Client(server.server) as client:
        await client.list_tools()
        customer = await client.call_tool(
            "customers_create", {"data": {"name": "ACME", "tax_id": "B33"}}
        )
        cid = customer.structured_content["id"]
        for _ in range(3):
            await client.call_tool(
                "invoices_create", {"data": {"customer": "x", "amount": 1, "customer_id": cid}}
            )
        page = await client.call_tool("customers_invoices", {"id": cid, "limit": 2})
        assert not page.is_error, page.content
        assert page.structured_content["total"] == 3
        assert len(page.structured_content["items"]) == 2
        one = await client.call_tool("customers_get", {"id": cid, "expand": ["invoices:2"]})
        embedded = one.structured_content["invoices"]
        assert embedded["total"] == 3 and embedded["has_more"] is True
        assert len(embedded["items"]) == 2
        missing = await client.call_tool("customers_invoices", {"id": 404})
        assert missing.structured_content["error"]["code"] == "RE-E102"


async def test_many_to_many_tool(manager):
    server = ResourceMcpServer(registry, manager)
    assert (
        "linked through invoice_tags"
        in server.tools["invoices_get"].to_mcp().input_schema["properties"]["expand"]["description"]
    )
    async with Client(server.server) as client:
        await client.list_tools()
        created = await client.call_tool(
            "invoices_create", {"data": {"customer": "x", "amount": 1}}
        )
        iid = created.structured_content["id"]
        tag = await client.call_tool("tags_create", {"data": {"name": "urgent"}})
        await client.call_tool(
            "invoice_tags_create",
            {"data": {"invoice_id": iid, "tag_id": tag.structured_content["id"]}},
        )
        tags = await client.call_tool("invoices_tags", {"id": iid})
        assert [t["name"] for t in tags.structured_content["items"]] == ["urgent"]
        nested = await client.call_tool("invoices_get", {"id": iid, "expand": ["tags.invoices"]})
        assert not nested.is_error, nested.content
        inner = nested.structured_content["tags"]["items"][0]["invoices"]
        assert inner["total"] == 1
