"""Polymorphic resources: a base (requests) with subtypes (vacation, expense)
stored as one family of records."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Reference, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import (
    ConfigurationError,
    EntityNotFound,
    OperationNotEnabled,
    ReferenceInUse,
    UnknownTransition,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore
from statemachine import State, StateMachine

calls: list[str] = []


class RequestFlow(StateMachine):
    """Request flow."""

    open = State(initial=True)
    granted = State(final=True)
    grant = open.to(granted)


@dataclass
class Request:
    title: str
    type: str | None = None
    status: str | None = None
    version: int | None = None
    id: int | None = None


@dataclass
class Vacation(Request):
    days: int = 0


@dataclass
class Expense(Request):
    amount: Decimal = Decimal(0)
    iban: str | None = None


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    type: str
    status: str
    version: int


class VacationIn(BaseModel):
    title: str
    days: int


class VacationRead(RequestRead):
    days: int


class ExpenseIn(BaseModel):
    title: str
    amount: Decimal
    iban: str | None = None


class ExpenseRead(RequestRead):
    amount: Decimal
    iban: str | None = None


def shared(ctx):
    calls.append(f"base:{ctx.operation}:{ctx.resource.name}")


def own(ctx):
    calls.append(f"own:{ctx.operation}:{ctx.resource.name}")


class RequestResource(Resource):
    """Every request."""

    name = "requests"
    model = Request
    schemas = {"read": RequestRead}
    operations = ["list", "get"]
    discriminator = "type"
    state_machine = RequestFlow
    version_field = "version"
    filterable = ["status"]
    sortable = ["id"]
    hooks = {"before_create": [shared], "before_transition": [shared]}


class VacationResource(Resource):
    """Vacation requests."""

    name = "vacation_requests"
    extends = RequestResource
    identity = "vacation"
    model = Vacation
    schemas = {"create": VacationIn, "update": VacationIn, "read": VacationRead}
    operations = ["list", "get", "create", "update", "delete"]
    hooks = {"before_create": [own]}


class ExpenseResource(Resource):
    """Expense claims."""

    name = "expense_requests"
    extends = RequestResource
    identity = "expense"
    model = Expense
    schemas = {"create": ExpenseIn, "read": ExpenseRead}
    operations = ["list", "get", "create", "delete"]
    restricted_fields = ["iban"]


@dataclass
class Comment:
    text: str
    request_id: int
    id: int | None = None


class CommentIn(BaseModel):
    text: str
    request_id: int


class CommentRead(CommentIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CommentResource(Resource):
    name = "comments"
    model = Comment
    schemas = {"create": CommentIn, "read": CommentRead}
    operations = ["create", "get"]
    references = {"request_id": Reference("requests")}  # any request


@pytest.fixture
def registry() -> ResourceRegistry:
    registry = ResourceRegistry()
    registry.register(VacationResource)  # brings its base
    registry.register(ExpenseResource)
    registry.register(CommentResource)
    return registry


@pytest.fixture
def manager(registry) -> ResourceManager:
    calls.clear()
    return ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)


async def test_one_family_of_records(manager, registry):
    assert [d.name for d in registry][:3] == ["requests", "vacation_requests", "expense_requests"]
    vacation = await manager.create("vacation_requests", {"title": "Summer", "days": 10})
    expense = await manager.create("expense_requests", {"title": "Taxi", "amount": "12.5"})
    assert (vacation.id, expense.id) == (1, 2)  # one id space
    assert (vacation.type, vacation.status, vacation.version) == ("vacation", "open", 1)
    assert calls == [
        "base:create:vacation_requests",
        "own:create:vacation_requests",
        "base:create:expense_requests",
    ]

    every = await manager.list("requests", ListQuery(order_by=["id"]))
    assert [type(r).__name__ for r in every.items] == ["VacationRead", "ExpenseRead"]
    assert every.items[0].days == 10 and every.items[1].amount == Decimal("12.5")
    only = await manager.list("requests", ListQuery(filters={"type": "expense"}))
    assert [r.id for r in only.items] == [2]
    assert (await manager.list("vacation_requests")).total == 1
    with pytest.raises(EntityNotFound):  # another type's id
        await manager.get("vacation_requests", expense.id)
    assert (await manager.get("requests", vacation.id)).days == 10


async def test_shared_transitions_and_writes(manager):
    vacation = await manager.create("vacation_requests", {"title": "Summer", "days": 10})
    granted = await manager.transition("requests", vacation.id, "grant")
    assert type(granted).__name__ == "VacationRead" and granted.status == "granted"
    assert calls[-1] == "base:transition:requests"
    with pytest.raises(UnknownTransition, match="run on requests"):
        await manager.transition("vacation_requests", vacation.id, "grant")
    updated = await manager.update("vacation_requests", vacation.id, {"title": "Winter", "days": 3})
    assert (updated.title, updated.status, updated.version) == ("Winter", "granted", 3)
    with pytest.raises(OperationNotEnabled):
        await manager.create("requests", {"title": "x"})


async def test_references_to_any_subtype(manager):
    expense = await manager.create("expense_requests", {"title": "Taxi", "amount": "1"})
    await manager.create("comments", {"text": "ok", "request_id": expense.id})
    with pytest.raises(ReferenceInUse):  # the base's dependents count too
        await manager.delete("expense_requests", expense.id)


async def test_reads_through_the_base_hide_subtype_restricted_fields(registry):
    from resource_engine.authz import AuthzConfig, IdpRoleSource, SystemRole
    from resource_engine.errors import PermissionDenied

    registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    admin = Principal(subject="root", issuer="i", roles=frozenset({"admins"}))
    reader = Principal(subject="ada", issuer="i", roles=frozenset({"readers"}))
    config = AuthzConfig(
        system_roles=[SystemRole.admin()],
        role_source=IdpRoleSource({"admins": "admin", "readers": "reader"}),
    )
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=PolicyAuthorizer(config, store.unit_of_work)
    )
    await manager.create("authz_roles", {"name": "reader"}, actor=admin)
    await manager.create(
        "authz_policies",
        {
            "name": "Readers see every request",
            "effect": "allow",
            "subjects": {"roles": ["reader"]},
            "actions": ["requests:list", "requests:get"],
        },
        actor=admin,
    )
    expense = await manager.create(
        "expense_requests", {"title": "Taxi", "amount": "1", "iban": "ES00"}, actor=admin
    )
    seen = await manager.get("requests", expense.id, actor=reader)  # base grant covers types
    assert seen.amount == Decimal(1) and "iban" not in seen.model_dump()
    with pytest.raises(PermissionDenied):  # the subtype's own routes need its grants
        await manager.get("expense_requests", expense.id, actor=reader)
    with pytest.raises(PermissionDenied):
        await manager.create("expense_requests", {"title": "x", "amount": "1"}, actor=reader)


class NoStatusRead(BaseModel):
    id: int
    title: str
    type: str
    version: int


class TypedIn(VacationIn):
    type: str


def _subtype(**attrs):
    attrs = {
        "name": "others",
        "extends": RequestResource,
        "model": Vacation,
        "schemas": {"create": VacationIn, "read": VacationRead},
        "operations": ["create", "get"],
        **attrs,
    }
    return type("OtherResource", (Resource,), attrs).definition()


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        ({"model": Comment}, "must subclass the base model"),
        (
            {"schemas": {"create": VacationIn, "read": NoStatusRead}},
            r"lacks the base's fields \['status'\]",
        ),
        ({"state_machine": RequestFlow}, "belong to its base"),
        ({"version_field": "other"}, "differs from the base's"),
        ({"extends": CommentResource}, "has no discriminator"),
        (
            {"schemas": {"create": TypedIn, "read": VacationRead}},
            "discriminator field 'type' is part of the 'create' schema",
        ),
    ],
)
def test_subtype_rules(attrs, message):
    with pytest.raises(ConfigurationError, match=message):
        _subtype(**attrs)


def test_base_rules():
    with pytest.raises(ConfigurationError, match="only lists and reads"):
        type(
            "B",
            (Resource,),
            {
                "name": "b",
                "model": Request,
                "schemas": {"read": RequestRead, "create": VacationIn},
                "operations": ["create"],
                "discriminator": "type",
            },
        ).definition()
    registry = ResourceRegistry()
    registry.register(VacationResource)
    twin = type(
        "Twin",
        (Resource,),
        {
            "name": "twins",
            "extends": RequestResource,
            "identity": "vacation",
            "model": Vacation,
            "schemas": {"read": VacationRead},
            "operations": ["get"],
        },
    )
    with pytest.raises(ConfigurationError, match="both have identity"):
        registry.register(twin)
