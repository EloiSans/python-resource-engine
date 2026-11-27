"""Firestore repositories against the emulator (compose, :8086): the
repository, condition and version contracts, commits and indexes."""

from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from resource_engine import ListQuery, Resource, ResourceRegistry
from resource_engine.capabilities import check_query
from resource_engine.errors import ConditionNotSupported, QueryNotSupported, TransactionTooLarge
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    CONTRACT_VERSIONED,
    ConditionContract,
    ContractItem,
    VersionContract,
    contract_registry,
)
from resource_engine_gcp.firestore import FirestoreStore, compile_condition

URL = "http://127.0.0.1:8086"
PROJECT = "resource-engine-test"


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8086), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.emulator("firestore")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason="Firestore emulator not reachable on 127.0.0.1:8086"))


@pytest.fixture
async def http():
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


def store_for(http, registry, names, **kw):
    suffix = uuid.uuid4().hex[:10]
    return FirestoreStore(
        http,
        registry,
        project=PROJECT,
        url=URL,
        collections={name: f"{name}-{suffix}" for name in names},
        **kw,
    )


@pytest.fixture
def contract_store(http):
    return store_for(http, contract_registry(), [CONTRACT_RESOURCE, CONTRACT_VERSIONED])


class TestFirestoreRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


class TestFirestoreVersions(VersionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


async def test_commit_is_one_batch_and_bounded(contract_store):
    async with contract_store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        kept = await repo.add(ContractItem("kept", 1))
        await repo.delete(await repo.add(ContractItem("dropped", 2)))
        await uow.commit()
    with pytest.raises(TransactionTooLarge):
        async with contract_store.unit_of_work() as uow:
            for n in range(501):
                await uow.repository(CONTRACT_RESOURCE).add(ContractItem(f"n{n}", n))
            await uow.commit()
    async with contract_store.unit_of_work() as uow:
        page = await uow.repository(CONTRACT_RESOURCE).list(ListQuery())
        assert [i.id for i in page.items] == [kept.id] and page.total == 1


async def test_sortable_fields_are_declared(http):
    store = store_for(
        http, contract_registry(), [CONTRACT_RESOURCE], sortable={CONTRACT_RESOURCE: ["rank"]}
    )
    async with store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        check_query(CONTRACT_RESOURCE, repo, ListQuery(order_by=["-rank"]))
        with pytest.raises(QueryNotSupported) as raised:
            check_query(CONTRACT_RESOURCE, repo, ListQuery(order_by=["name"]))
        assert raised.value.details["order_by"] == ["name"]


def test_conditions_compile_without_not():
    rank = {"attr": "resource.rank"}
    assert compile_condition({"not": {"lt": [rank, 3]}}) == {
        "fieldFilter": {
            "field": {"fieldPath": "rank"},
            "op": "GREATER_THAN_OR_EQUAL",
            "value": {"integerValue": "3"},
        }
    }
    assert compile_condition({"gt": [3, rank]})["fieldFilter"]["op"] == "LESS_THAN"
    assert compile_condition({"and": [True, {"in": [rank, []]}]}) is False
    with pytest.raises(ConditionNotSupported):
        compile_condition({"not": {"exists": rank}})


@dataclass
class Payment:
    amount: Decimal
    paid_on: date
    at: datetime
    tags: list[str]
    reference: str | None = None
    id: str | None = None


class PaymentResource(Resource):
    model = Payment
    schemas = {}


async def test_types_round_trip(http):
    registry = ResourceRegistry()
    registry.register(PaymentResource)
    store = store_for(http, registry, ["payments"])
    payment = Payment(Decimal("12.5"), date(2026, 3, 1), datetime(2026, 3, 1, 9, tzinfo=UTC), ["a"])
    async with store.unit_of_work() as uow:
        await uow.repository("payments").add(payment)
        await uow.commit()
    async with store.unit_of_work() as uow:
        assert await uow.repository("payments").get(payment.id) == payment
