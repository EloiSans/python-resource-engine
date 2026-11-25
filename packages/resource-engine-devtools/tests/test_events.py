"""Declared domain events: RE-C006, generated docs and the AsyncAPI command."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from resource_engine import Action, DomainEvent, Resource, ResourceRegistry
from resource_engine.messaging import EventRouter
from resource_engine_devtools.cli import main
from resource_engine_devtools.documentation import introspect, validate
from resource_engine_devtools.documentation.generators import render_markdown
from resource_engine_devtools.events_check import check_events


@dataclass(frozen=True, kw_only=True)
class OrderShipped(DomainEvent):
    """The warehouse handed the order to the carrier."""

    order_id: int
    carrier: str


@dataclass(frozen=True, kw_only=True)
class OrderLost(DomainEvent):
    order_id: int


@dataclass
class Order:
    total: int
    id: int | None = None


class OrderIn(BaseModel):
    total: int


class OrderOut(OrderIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


def record_shipped(ctx):
    ctx.record_event(OrderShipped(order_id=ctx.entity.id, carrier="UPS"))


def record_lost(ctx):
    _lose(ctx)


def _lose(ctx):
    ctx.record_event(OrderLost(order_id=ctx.entity.id))


class Ship(Action):
    """Ships the order.

    Postconditions:
        The order is on its way.
    """

    name = "ship"
    instance = True

    async def execute(self, ctx):
        ctx.record_event(OrderShipped(order_id=ctx.entity.id, carrier="DHL"))


def registry(events=(), **hooks) -> ResourceRegistry:
    class OrderResource(Resource):
        """Orders.

        Business area:
            Sales.
        """

        name = "orders"
        model = Order
        schemas = {"create": OrderIn, "read": OrderOut}
        operations = ["get", "create"]
        actions = [Ship]

    OrderResource.hooks = hooks
    OrderResource.events = events
    reg = ResourceRegistry()
    reg.register(OrderResource)
    return reg


def test_undeclared_events_are_reported():
    found = check_events(registry(after_create=record_lost))
    assert [(v.code, "OrderShipped" in v.message) for v in found] == [
        ("RE-C006", False),  # the hook, through _lose
        ("RE-C006", True),  # the action
    ]
    assert "_lose" in found[0].message
    assert "OrderResource.events" in found[1].hint


def test_declared_events_pass():
    assert check_events(registry([OrderShipped], after_create=record_shipped)) == []


def test_events_in_the_generated_docs():
    model = introspect(registry([OrderShipped, OrderLost]))
    [resource] = model.resources
    assert [e.name for e in resource.events] == ["OrderShipped", "OrderLost"]
    assert [f.name for f in resource.events[0].fields] == ["order_id", "carrier"]
    page = render_markdown(model)["resources/orders.md"]
    assert "## Esdeveniments de domini" in page
    assert "| `carrier` | `str` | sí |" in page
    findings = [f for f in validate(model) if f.code == "RE-D011"]
    assert {(f.target.rsplit(".", 1)[-1], f.level) for f in findings} == {
        ("OrderShipped", "ok"),
        ("OrderLost", "warning"),
    }


def _project(tmp_path, monkeypatch, config: str = "") -> None:
    module = type(sys)("events_app")
    module.registry = registry([OrderShipped])
    router = EventRouter(name="orders-returns")
    router.register("ReturnRequested", lambda envelope, data: None, payload=OrderIn)
    module.returns = router
    module.make_router = lambda: router
    monkeypatch.setitem(sys.modules, "events_app", module)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.resource-engine]\nregistry = "events_app:registry"\n' + config
    )
    monkeypatch.chdir(tmp_path)


def test_asyncapi_command(tmp_path, monkeypatch, capsys):
    _project(
        tmp_path,
        monkeypatch,
        'consumers = ["events_app:returns"]\n\n[tool.resource-engine.asyncapi]\n'
        'title = "Orders"\nformat = "cloudevents"\n'
        '[tool.resource-engine.asyncapi.servers.rabbit]\nhost = "mq:5672"\nprotocol = "amqp"\n'
        '[tool.resource-engine.asyncapi.channels.orders]\naddress = "orders.events"\n'
        'servers = ["rabbit"]\n',
    )
    assert main(["docs", "asyncapi"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["info"]["title"] == "Orders"
    assert doc["channels"]["orders"]["address"] == "orders.events"
    assert set(doc["channels"]["orders"]["messages"]) == {
        "ResourceCreated",
        "ActionExecuted",
        "OrderShipped",
    }
    assert "ReturnRequested" in doc["channels"]["orders-returns"]["messages"]
    assert doc["servers"]["rabbit"]["protocol"] == "amqp"

    out = tmp_path / "asyncapi.json"
    args = ["docs", "asyncapi", "--consumer", "events_app:make_router", "--out", str(out)]
    assert main(args) == 0
    assert main(["docs", "asyncapi", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["asyncapi"] == "3.0.0"


def test_unknown_asyncapi_keys(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, '\n[tool.resource-engine.asyncapi]\ntitel = "typo"\n')
    try:
        main(["docs", "asyncapi"])
    except SystemExit as exc:
        assert "titel" in str(exc)
    else:
        raise AssertionError("unknown key accepted")


def test_docs_generate_writes_the_asyncapi_document(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    assert main(["docs", "generate", "--out", str(tmp_path / "docs")]) == 0
    doc = json.loads((tmp_path / "docs" / "asyncapi.json").read_text())
    assert "orders" in doc["channels"]
