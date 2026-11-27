"""``resource-engine mcp``: the development CLI as MCP tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters  # noqa: E402
from resource_engine_devtools.mcp_server import DevServer, explain_error  # noqa: E402

REPO = Path(__file__).resolve().parents[3]

APP = '''
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceRegistry


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIO(BaseModel):
    """A note."""

    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    text: str


class NoteResource(Resource):
    """Notes.

    Business area:
        Testing
    """

    model = Note
    schemas = {"create": NoteIO, "read": NoteIO}
    operations = OPERATIONS


registry = ResourceRegistry()
registry.register(NoteResource)
'''


def _project(tmp_path: Path, operations: str = '["get"]') -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.resource-engine]\nregistry = "devapp:registry"\n', encoding="utf-8"
    )
    (tmp_path / "devapp.py").write_text(APP.replace("OPERATIONS", operations), encoding="utf-8")
    return tmp_path


async def test_tools_run_against_the_code_as_it_is_now(tmp_path):
    project = _project(tmp_path)
    async with Client(DevServer(project).build()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"check", "inspect", "docs_validate", "scaffold_resource"} <= names
        first = await client.call_tool("inspect", {})
        assert first.structured_content["output"][0]["operations"] == ["get"]
        _project(tmp_path, '["get", "create"]')  # the agent edits the code
        again = await client.call_tool("inspect", {"resource": "notes"})
        assert again.structured_content["output"][0]["operations"] == ["get", "create"]
        check = await client.call_tool("check", {})
        assert not check.is_error and check.structured_content["output"]["passed"] is True


async def test_findings_are_results_not_errors(tmp_path):
    project = _project(tmp_path)
    (project / "domain").mkdir()
    (project / "domain" / "bad.py").write_text("import sqlalchemy\n", encoding="utf-8")
    async with Client(DevServer(project).build()) as client:
        check = await client.call_tool("check", {})
        assert not check.is_error
        assert check.structured_content["exit_code"] == 1
        codes = [v["code"] for v in check.structured_content["output"]["violations"]]
        assert codes == ["RE-C001"]
        docs = await client.call_tool("docs_validate", {"strict": True})
        assert not docs.is_error  # findings are the tool's answer
        findings = docs.structured_content["output"]["findings"]
        assert findings and all(f["level"] != "ok" for f in findings)


async def test_scaffold(tmp_path):
    project = _project(tmp_path)
    args = {
        "name": "orders",
        "package": "devshop.orders",
        "fields": ["customer:str", "total:Decimal"],
        "root": str(tmp_path),
    }
    async with Client(DevServer(project).build()) as client:
        dry = await client.call_tool("scaffold_resource", {**args, "dry_run": True})
        assert dry.structured_content["output"]["dry_run"] is True
        assert not (tmp_path / "devshop").exists()
        made = await client.call_tool("scaffold_resource", args)
        assert not made.is_error
        assert (tmp_path / "devshop" / "orders" / "application" / "resource.py").is_file()
        assert made.structured_content["output"]["next_steps"]
        again = await client.call_tool("scaffold_resource", args)
        assert again.is_error  # exists: refused (exit status 2)
        consumer = await client.call_tool(
            "scaffold_consumer",
            {
                "name": "invoice_events",
                "package": "devshop.billing",
                "asyncapi": str(REPO / "examples/invoices/docs/asyncapi.json"),
                "channels": ["invoices"],
                "root": str(tmp_path),
            },
        )
        assert not consumer.is_error
        assert (tmp_path / "devshop/billing/consumers/invoice_events.py").is_file()
        base = {"package": "devshop.orders", "root": str(tmp_path), "resource": "orders"}
        for tool, extra in (
            ("scaffold_hook", {"function": "check_total", "on": ["before_create"]}),
            ("scaffold_event", {"event_class": "OrderPlaced", "fields": ["total:Decimal"]}),
            ("scaffold_slot", {"slot": "receipt", "content_types": ["application/pdf"]}),
            ("scaffold_reference", {"field": "customer_id", "to": "customers"}),
            ("scaffold_policy", {"role": "clerk", "read_only": True}),
        ):
            part = await client.call_tool(tool, {**base, **extra})
            assert not part.is_error, (tool, part.structured_content)
        resource = (tmp_path / "devshop/orders/application/resource.py").read_text()
        assert "hooks.check_total" in resource and '"receipt",' in resource
        assert "events = [OrderPlaced]" in resource and '"customer_id": Reference(' in resource
        assert "Clerk reads orders" in (tmp_path / "devshop/orders/authz.py").read_text()


async def test_bad_calls(tmp_path):
    async with Client(DevServer(_project(tmp_path)).build()) as client:
        assert (await client.call_tool("explain_error", {"code": "RE-E999"})).is_error
        assert (await client.call_tool("inspect", {"nope": 1})).is_error
        assert (await client.call_tool("scaffold_action", {})).is_error
        assert (await client.call_tool("deploy", {})).is_error


def test_explain_error():
    found = explain_error("re-e702")
    assert found["error_class"] == "resource_engine.errors.PermissionDenied"
    assert found["catalogue"]
    assert explain_error("RE-C006")["catalogue"]
    ranged = explain_error("RE-D004")["catalogue"]
    assert any("States" in row for row in ranged)


async def test_resources(tmp_path):
    async with Client(DevServer(_project(tmp_path)).build()) as client:
        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert "resource-engine://skill/references/lifecycle.md" in uris
        assert "resource-engine://project" in uris
        lifecycle = await client.read_resource("resource-engine://skill/references/lifecycle.md")
        assert "before_validate" in lifecycle.contents[0].text
        catalogue = await client.read_resource("resource-engine://project")
        assert "notes" in catalogue.contents[0].text
        with pytest.raises(Exception, match="Unknown resource"):
            await client.read_resource("resource-engine://skill/../../cli.py")


async def test_command_over_stdio(tmp_path):
    project = _project(tmp_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "resource_engine_devtools.cli", "mcp", "--project", str(project)],
    )
    async with Client(params) as client:
        result = await client.call_tool("inspect", {})
        assert result.structured_content["output"][0]["name"] == "notes"


def test_without_the_extra(monkeypatch):
    from resource_engine_devtools.cli import main

    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit, match=re.escape("resource-engine-devtools[mcp]")):
        main(["mcp"])
