"""RE-C004 (operations nobody but a catch-all role can perform) and RE-C005
(registry refused by the engine)."""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Action, Resource, ResourceRegistry
from resource_engine.authz import AuthzConfig, Policy, SystemRole
from resource_engine_devtools.cli import main
from resource_engine_devtools.gates_check import check_gates, load_policies


@dataclass
class Ticket:
    title: str
    id: int | None = None


class TicketIn(BaseModel):
    title: str


class TicketOut(TicketIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class Close(Action):
    """Tanca el tiquet."""

    async def execute(self, ctx):
        return None


class TicketResource(Resource):
    """Tiquets de suport."""

    model = Ticket
    schemas = {"create": TicketIn, "read": TicketOut}
    operations = ["list", "get", "create", "delete"]
    actions = [Close]


def registry(cls=TicketResource):
    registry = ResourceRegistry()
    registry.register(cls)
    registry.register_authz()
    registry.register_audit()
    return registry


CONFIG = AuthzConfig(system_roles=[SystemRole.admin(), SystemRole.viewer()])


def ungated(policies, config=CONFIG, cls=TicketResource):
    return sorted(v.message.split(" ")[0] for v in check_gates(registry(cls), config, policies))


def test_actions_only_admin_can_perform():
    assert ungated([]) == ["tickets:action:close", "tickets:create", "tickets:delete"]
    # viewer grants list and get; admin ("*") is the catch-all and does not count


def test_seed_policies_close_the_gap():
    policies = [
        {"name": "Agents", "effect": "allow", "actions": ["tickets:create", "tickets:action:*"]},
        {"name": "Off", "effect": "allow", "actions": ["tickets:delete"], "enabled": False},
        {"name": "Deny", "effect": "deny", "actions": ["tickets:delete"]},
    ]
    assert ungated(policies) == ["tickets:delete"]


def test_policy_objects_and_system_roles_count():
    policy = Policy(id="p1", name="Leads", effect="allow", actions=["tickets:*"])
    assert ungated([vars(policy)]) == []
    clerk = SystemRole(name="clerk", grants=["tickets:create", "tickets:delete", "*:action:*"])
    config = AuthzConfig(system_roles=[SystemRole.admin(), clerk])
    assert ungated([], config) == ["tickets:get", "tickets:list"]


def test_sensitive_resources_need_explicit_names():
    class SecretResource(TicketResource):
        name = "secrets"
        authz_wildcards = False

    broad = [{"name": "All", "effect": "allow", "actions": ["*:*", "*:action:*"]}]
    assert "secrets:create" in ungated(broad, cls=SecretResource)
    named = [{"name": "Named", "effect": "allow", "actions": ["secrets:*"]}]
    assert ungated(named, cls=SecretResource) == []


def test_violation_points_at_the_resource():
    (violation, *_) = check_gates(registry(), CONFIG, [])
    assert violation.code == "RE-C004" and violation.path == __file__
    source = Path(__file__).read_text().splitlines()
    assert "class TicketResource" in source[violation.line - 1]


def test_load_policies_from_a_module(monkeypatch):
    module = type(sys)("seed_mod")
    module.SEED_POLICIES = [{"name": "x", "effect": "allow", "actions": ["a:b"]}]
    module.MORE = [Policy(id="p", name="y", effect="allow", actions=["c:d"])]
    monkeypatch.setitem(sys.modules, "seed_mod", module)
    names = [p["name"] for p in load_policies(["seed_mod", "seed_mod:MORE"])]
    assert names == ["x", "y"]


BROKEN = textwrap.dedent(
    """
    from dataclasses import dataclass

    from pydantic import BaseModel
    from statemachine import State, StateMachine
    from resource_engine import Resource, ResourceRegistry


    @dataclass
    class Order:
        status: str | None = None
        id: int | None = None


    class OrderIn(BaseModel):
        status: str


    class Flow(StateMachine):
        new = State(initial=True)
        done = State(final=True)
        finish = new.to(done)


    class OrderResource(Resource):
        model = Order
        schemas = {"create": OrderIn, "read": OrderIn}
        operations = ["create"]
        state_machine = Flow


    registry = ResourceRegistry()
    registry.register(OrderResource)
    """
)


@pytest.fixture
def broken_app(tmp_path, monkeypatch):
    (tmp_path / "broken_app.py").write_text(BROKEN)
    monkeypatch.chdir(tmp_path)
    yield tmp_path / "broken_app.py"
    sys.modules.pop("broken_app", None)


def test_refused_declaration_is_a_violation(broken_app, capsys):
    assert main(["check", "--json", "--app", "broken_app:registry", "."]) == 1
    (violation,) = json.loads(capsys.readouterr().out)["violations"]
    assert violation["code"] == "RE-C005" and "RE-E004" in violation["message"]
    assert violation["path"] == str(broken_app)
    lines = broken_app.read_text().splitlines()
    assert lines[violation["line"] - 1] == "registry.register(OrderResource)"
    assert violation["hint"]


def test_check_reads_the_seed_from_pyproject(tmp_path, monkeypatch, capsys):
    module = type(sys)("tickets_app")
    module.registry = registry()
    module.authz = CONFIG
    module.SEED_POLICIES = [{"name": "All", "effect": "allow", "actions": ["tickets:*"]}]
    monkeypatch.setitem(sys.modules, "tickets_app", module)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.resource-engine]\nregistry = "tickets_app:registry"\n'
        'authz = "tickets_app:authz"\npolicies = "tickets_app:SEED_POLICIES"\n'
    )
    monkeypatch.chdir(tmp_path)
    assert main(["check", "."]) == 0
    capsys.readouterr()
    module.SEED_POLICIES = []
    assert main(["check", "--json", "."]) == 1
    codes = {v["code"] for v in json.loads(capsys.readouterr().out)["violations"]}
    assert codes == {"RE-C004"}
