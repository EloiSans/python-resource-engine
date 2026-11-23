from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import AuthzConfig, EngineAuthorizer, IdpRoleSource, SystemRole
from resource_engine.errors import ConditionNotSupported, FieldNotWritable, PermissionDenied
from resource_engine.ports.identity import Principal
from resource_engine.testing import ConditionEngine, InMemoryStore
from resource_engine.testing.contracts import AuthorizerContract

OWNER = {"eq": [{"attr": "resource.owner"}, {"attr": "subject.id"}]}
RULES = {
    "contract_docs:get": OWNER,
    "contract_docs:list": OWNER,
    "contract_docs:update": OWNER,
    "contract_docs:create": {"contains": [{"attr": "subject.roles"}, "writer"]},
    "contract_docs:read:secret": {"contains": [{"attr": "subject.roles"}, "auditor"]},
}


class TestConditionEngine(AuthorizerContract):
    @pytest.fixture
    def contract_engine(self):
        return ConditionEngine(RULES)


@dataclass
class Bill:
    owner: str
    amount: Decimal
    note: str | None = None
    id: int | None = None


class BillIn(BaseModel):
    owner: str
    amount: Decimal
    note: str | None = None


class BillPatch(BaseModel):
    amount: Decimal | None = None
    note: str | None = None


class BillOut(BillIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class BillResource(Resource):
    model = Bill
    schemas = {"create": BillIn, "update": BillPatch, "read": BillOut}
    operations = ["list", "get", "create", "update"]
    restricted_fields = ["note"]


ADA = Principal(subject="ada", issuer="idp", roles=frozenset({"clerks"}))
ROOT = Principal(subject="root", issuer="idp", roles=frozenset({"admins"}))


def build(rules):
    registry = ResourceRegistry()
    registry.register(BillResource)
    store = InMemoryStore.from_registry(registry)
    engine = ConditionEngine(rules)
    config = AuthzConfig(
        system_roles=[SystemRole.admin()],
        role_source=IdpRoleSource({"clerks": "clerk", "admins": "admin"}),
    )
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=EngineAuthorizer(engine, config)
    )
    return manager, engine


async def test_system_roles_are_decided_before_the_engine():
    manager, engine = build({})
    bill = await manager.create("bills", {"owner": "x", "amount": 1, "note": "n"}, actor=ROOT)
    assert (await manager.get("bills", bill.id, actor=ROOT)).note == "n"
    assert engine.requests == []


async def test_requests_carry_plain_values_and_partial_answers():
    small = {"le": [{"attr": "input.amount"}, 100]}
    manager, engine = build({"bills:create": small, "bills:get": True})
    authz = manager.authorizer
    step2 = await authz.check(ADA, "bills:create")
    assert step2.allowed is None  # depends on the input
    request = engine.requests[-1]
    assert request.unknowns == ("resource", "input") and request.resource_type == "bills"
    assert request.subject["roles"] == ["clerk"]
    await manager.create("bills", {"owner": ADA.id, "amount": "50"}, actor=ADA)
    known = [r for r in engine.requests if r.action == "bills:create"][-1]
    assert known.input["amount"] == Decimal("50") and known.unknowns == ()
    with pytest.raises(PermissionDenied):
        await manager.create("bills", {"owner": ADA.id, "amount": "500"}, actor=ADA)


async def test_residual_on_input_is_evaluated_locally():
    # the engine leaves an input condition even with the record known
    manager, engine = build({"bills:update": {"ne": [{"attr": "input.note"}, "x"]}})
    authz = manager.authorizer
    record = Bill(ADA.id, Decimal(1), id=1)
    decision = await authz.check(ADA, "bills:update", resource=record)
    assert decision.allowed is None
    assert (await authz.check(ADA, "bills:update", resource=record, input={"note": "y"})).allowed


async def test_restricted_fields_are_asked_once_and_decided_per_record():
    mine = {"eq": [{"attr": "resource.owner"}, {"attr": "subject.id"}]}
    manager, engine = build(
        {"bills:list": True, "bills:get": True, "bills:update": True, "bills:*:note": mine}
    )
    for owner in (ADA.id, "other"):
        await manager.create("bills", {"owner": owner, "amount": 1, "note": "n"}, actor=ROOT)
    engine.requests.clear()
    page = await manager.list("bills", actor=ADA)
    notes = {b.owner: getattr(b, "note", "hidden") for b in page.items}
    assert notes == {ADA.id: "n", "other": "hidden"}
    asked = [r.action for r in engine.requests]
    assert asked.count("bills:read:note") == 1 and "bills:read:amount" not in asked
    with pytest.raises(FieldNotWritable):
        await manager.update("bills", 2, {"note": "x"}, actor=ADA)
    me = await manager.authorizer.field_permissions(ADA)
    assert me["bills"]["conditional"] == ["note"]


async def test_untranslatable_scope_fails_closed():
    class Refusing(ConditionEngine):
        async def evaluate(self, request):
            if request.action == "bills:list":
                raise ConditionNotSupported("regex in a row filter")
            return True

    registry = ResourceRegistry()
    registry.register(BillResource)
    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=EngineAuthorizer(Refusing({}))
    )
    with pytest.raises(ConditionNotSupported):
        await manager.list("bills", actor=ADA)
