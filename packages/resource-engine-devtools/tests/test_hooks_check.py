"""RE-C002 / RE-C003: hooks whose effects do not match their lifecycle point."""

from __future__ import annotations

import json
import smtplib
import sys
from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Action, Resource, ResourceRegistry, external_effect
from resource_engine_devtools.cli import main
from resource_engine_devtools.hooks_check import check_hooks


@dataclass
class Order:
    total: int
    status: str = "new"
    id: int | None = None


class OrderIn(BaseModel):
    total: int


class OrderOut(OrderIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


@external_effect
def send_invoice(ctx):
    """Marked: may only run after commit."""


def notify_warehouse(order_id):
    return httpx.post("https://warehouse.example/orders", json={"id": order_id})


def prepare(ctx):
    notify_warehouse(ctx.entity.id)


def calls_http(ctx):
    httpx.post("https://example.com/hook")


def calls_marked(ctx):
    send_invoice(ctx)


def clean(ctx):
    ctx.entity.total = round(ctx.entity.total)


def after_commit_bad(ctx):
    ctx.record_event(object())
    ctx.entity.status = "sent"
    ctx.repository().get(1)
    if ctx.uow:
        pass


def after_commit_fine(ctx):
    httpx.post("https://example.com/sent")
    send_invoice(ctx)


class Ship(Action):
    name = "ship"
    instance = True

    async def execute(self, ctx):
        self._mail(ctx)

    def _mail(self, ctx):
        with smtplib.SMTP("localhost") as smtp:
            smtp.sendmail("a@example.com", ["b@example.com"], "shipped")


def resource(**hooks):
    class OrderResource(Resource):
        name = "orders"
        model = Order
        schemas = {"create": OrderIn, "read": OrderOut}
        operations = ["get", "create"]

    OrderResource.hooks = hooks
    return OrderResource


def violations(**hooks):
    registry = ResourceRegistry()
    registry.register(resource(**hooks))
    return check_hooks(registry)


def test_http_client_in_a_hook_before_commit():
    (v,) = violations(before_create=calls_http)
    assert v.code == "RE-C002" and "uses httpx" in v.message and "before_create" in v.message
    assert v.path == __file__ and v.line == calls_http.__code__.co_firstlineno + 1


def test_followed_through_application_helpers():
    (v,) = violations(after_create=prepare)
    assert v.code == "RE-C002" and "via notify_warehouse" in v.message
    assert v.line == notify_warehouse.__code__.co_firstlineno + 1


def test_marked_function_called_before_commit():
    (v,) = violations(after_validate=calls_marked)
    assert "marked @external_effect" in v.message


def test_action_execute_and_its_methods():
    class ShippingResource(Resource):
        name = "orders"
        model = Order
        schemas = {"create": OrderIn, "read": OrderOut}
        operations = ["get", "create"]
        actions = [Ship]

    registry = ResourceRegistry()
    registry.register(ShippingResource)
    (v,) = check_hooks(registry)
    assert "action ship" in v.message and "smtplib" in v.message and "Ship._mail" in v.message


def test_after_commit_needing_the_transaction():
    found = violations(after_commit=after_commit_bad)
    assert [v.code for v in found] == ["RE-C003"] * 4
    text = " ".join(v.message for v in found)
    for part in ("records an event", "changes ctx.entity", "ctx.repository()", "ctx.uow"):
        assert part in text


def test_clean_hooks_pass():
    assert violations(before_create=clean, after_commit=after_commit_fine) == []


def test_check_command_reports_them(tmp_path, monkeypatch, capsys):
    module = type(sys)("hooks_app")
    registry = ResourceRegistry()
    registry.register(resource(before_create=calls_http))
    module.registry = registry
    monkeypatch.setitem(sys.modules, "hooks_app", module)
    monkeypatch.chdir(tmp_path)
    assert main(["check", "--json", "--app", "hooks_app:registry", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [v["code"] for v in report["violations"]] == ["RE-C002"]


def test_check_without_a_registry_still_lints_imports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["check", str(tmp_path)]) == 0


@pytest.mark.parametrize("hook", [lambda ctx: None, print])
def test_unreadable_sources_are_skipped(hook):
    assert violations(before_create=hook) == []
