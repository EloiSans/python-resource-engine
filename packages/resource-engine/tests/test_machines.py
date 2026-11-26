"""Several state machines per resource: declaration, transitions, guards across
machines, timers, history, events and the generated surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Machine, Resource, ResourceManager, ResourceRegistry, Timer, TimerJob
from resource_engine.application.events import TransitionExecuted
from resource_engine.errors import ConfigurationError, TransitionNotAllowed, UnknownTransition
from resource_engine.testing import InMemoryStore
from statemachine import State, StateMachine


class Approval(StateMachine):
    """Approval."""

    draft = State(initial=True)
    approved = State()
    approve = draft.to(approved)
    revoke = approved.to(draft)


class Shipping(StateMachine):
    """Shipping, once approved."""

    waiting = State(initial=True)
    shipped = State()
    lost = State(final=True)
    ship = waiting.to(shipped, cond="is_approved")
    lose = shipped.to(lost)

    def is_approved(self) -> bool:
        return self.model.status == "approved"


@dataclass
class Order:
    item: str
    status: str | None = None
    status_since: datetime | None = None
    shipping: str | None = None
    shipping_since: datetime | None = None
    id: int | None = None


class OrderIn(BaseModel):
    item: str


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item: str
    status: str
    status_since: datetime | None = None
    shipping: str
    shipping_since: datetime | None = None


class OrderResource(Resource):
    """Orders."""

    name = "orders"
    model = Order
    schemas = {"create": OrderIn, "update": OrderIn, "read": OrderOut}
    operations = ["list", "get", "create", "update"]
    machines = {
        "approval": Machine(Approval, field="status", since_field="status_since"),
        "shipping": Machine(Shipping, field="shipping", since_field="shipping_since"),
    }
    timers = [Timer("lose", after=timedelta(days=10))]


@pytest.fixture
def registry() -> ResourceRegistry:
    registry = ResourceRegistry()
    registry.register(OrderResource)
    registry.register_audit()
    return registry


@pytest.fixture
def manager(registry) -> ResourceManager:
    return ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work, audit=True)


async def test_each_machine_has_its_own_state(manager):
    order = await manager.create("orders", {"item": "book"})
    assert (order.status, order.shipping) == ("draft", "waiting")
    assert order.status_since and order.shipping_since
    assert await manager.allowed_transitions("orders", order.id) == ["approve"]  # guards run
    with pytest.raises(TransitionNotAllowed):  # the guard reads the other machine
        await manager.transition("orders", order.id, "ship")
    approved = await manager.transition("orders", order.id, "approve")
    assert approved.shipping_since == order.shipping_since  # only its own clock moves
    assert approved.status_since > order.status_since
    assert await manager.allowed_transitions("orders", order.id) == ["revoke", "ship"]
    shipped = await manager.transition("orders", order.id, "ship")
    assert (shipped.status, shipped.shipping) == ("approved", "shipped")
    assert shipped.status_since == approved.status_since
    with pytest.raises(UnknownTransition):
        await manager.transition("orders", order.id, "fly")


async def test_state_fields_stay_protected(manager):
    order = await manager.create("orders", {"item": "book"})
    async with manager.uow_factory() as uow:
        entity = await uow.repository("orders").get(order.id)
    OrderResource.apply_update(entity, OrderIn(item="pen"), None)  # type: ignore[arg-type]
    assert entity.shipping == "waiting"


async def test_history_and_events_name_the_machine(registry):
    events: list[TransitionExecuted] = []

    class Capture:
        async def publish(self, published):
            events.extend(e for e in published if isinstance(e, TransitionExecuted))

    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(registry, store.unit_of_work, audit=True, event_publisher=Capture())
    order = await manager.create("orders", {"item": "book"})
    await manager.transition("orders", order.id, "approve")
    await manager.transition("orders", order.id, "ship")
    assert [(e.machine, e.transition, e.source, e.target) for e in events] == [
        ("approval", "approve", "draft", "approved"),
        ("shipping", "ship", "waiting", "shipped"),
    ]
    history = await manager.history("orders", order.id)
    assert [(c.machine, c.operation, c.event, c.from_state, c.to_state) for c in history] == [
        ("approval", "create", None, None, "draft"),
        ("shipping", "create", None, None, "waiting"),
        ("approval", "transition", "approve", "draft", "approved"),
        ("shipping", "transition", "ship", "waiting", "shipped"),
    ]
    shipping = await manager.history("orders", order.id, machine="shipping")
    assert [c.to_state for c in shipping] == ["waiting", "shipped"]
    with pytest.raises(UnknownTransition):
        await manager.history("orders", order.id, machine="nope")


async def test_timers_watch_their_own_machine(manager, registry):
    [timer] = registry.get("orders").timers
    assert (timer.field, timer.since, timer.states) == ("shipping", "shipping_since", ("shipped",))
    order = await manager.create("orders", {"item": "book"})
    await manager.transition("orders", order.id, "approve")
    await manager.transition("orders", order.id, "ship")
    later = datetime.now(UTC) + timedelta(days=11)
    run = await TimerJob(manager, resources=["orders"], clock=lambda: later).run_once()
    assert run.fired == 1
    assert (await manager.get("orders", order.id)).shipping == "lost"


def test_definition_lists_every_machine(registry):
    d = registry.get("orders")
    assert list(d.machines) == ["approval", "shipping"]
    assert d.transitions == ("approve", "revoke", "ship", "lose")
    assert d.state_fields == ("status", "shipping")
    assert d.machine_for("ship").name == "shipping"
    assert d.machine is d.machines["approval"]  # the first one


def _declare(**attrs):
    attrs = {
        "name": "things",
        "model": Order,
        "schemas": {"create": OrderIn, "read": OrderOut},
        "operations": ["create", "get"],
        **attrs,
    }
    return type("ThingResource", (Resource,), attrs).definition()


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        (
            {"machines": {"a": Machine(Approval), "b": Machine(Approval, field="shipping")}},
            "is in machines 'a' and 'b'",
        ),
        (
            {"machines": {"a": Machine(Approval), "b": Machine(Shipping)}},
            "already used by machine 'a'",
        ),
        (
            {"machines": {"a": Machine(Approval)}, "state_machine": Approval},
            "both state_machine and machines",
        ),
        (
            {"machines": {"a": Machine(Approval)}, "state_since_field": "status_since"},
            "set since_field on each Machine",
        ),
        ({"machines": {"a": Approval}}, "use Machine("),
    ],
)
def test_configuration_errors(attrs, message):
    with pytest.raises(ConfigurationError, match=message.replace("(", r"\(")):
        _declare(**attrs)


def test_one_machine_shorthand_is_named_by_its_field():
    d = _declare(state_machine=Approval)
    assert list(d.machines) == ["status"]
    assert d.machine_for("approve").state_field == "status"
