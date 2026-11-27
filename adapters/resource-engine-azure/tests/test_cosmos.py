"""Cosmos DB repositories against the vNext emulator (compose, :8081): the
repository, condition and version contracts, batches per partition."""

from __future__ import annotations

import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from resource_engine import ListQuery, Resource, ResourceRegistry
from resource_engine.errors import TransactionTooLarge, VersionConflict
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    CONTRACT_VERSIONED,
    ConditionContract,
    ContractItem,
    VersionContract,
    contract_registry,
)

URL = "http://127.0.0.1:8081"
#: The emulator's well-known key (public, documented by Microsoft).
KEY = "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8081), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [pytest.mark.emulator("cosmos")]
if not _up():
    pytestmark.append(pytest.mark.skip(reason="Cosmos DB emulator not reachable on 127.0.0.1:8081"))


@pytest.fixture
async def database():
    from azure.cosmos.aio import CosmosClient

    async with CosmosClient(URL, KEY) as client:
        name = f"re-{uuid.uuid4().hex[:10]}"
        db = await client.create_database(name)
        yield db
        await client.delete_database(name)


async def container(database, partition_key="/id"):
    from azure.cosmos import PartitionKey

    name = f"c-{uuid.uuid4().hex[:10]}"
    await database.create_container(name, PartitionKey(partition_key))
    return name


@pytest.fixture
async def contract_store(database):
    from resource_engine_azure import CosmosStore

    return CosmosStore(
        database,
        contract_registry(),
        {
            CONTRACT_RESOURCE: await container(database),
            CONTRACT_VERSIONED: await container(database),
        },
    )


class TestCosmosRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


class TestCosmosVersions(VersionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


async def test_partitioned_batches(database):
    from resource_engine_azure import CosmosContainer, CosmosStore

    store = CosmosStore(
        database,
        contract_registry(),
        {
            CONTRACT_RESOURCE: CosmosContainer(
                await container(database, "/group"), partition_key="group"
            )
        },
    )
    async with store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        a = await repo.add(ContractItem("a", 1, "x"))
        await repo.add(ContractItem("b", 2, "y"))
        await uow.commit()
    async with store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        assert (await repo.get(a.id)).name == "a"  # found across partitions
        page = await repo.list(ListQuery(filters={"group": "y"}))
        assert [i.name for i in page.items] == ["b"] and page.total == 1
        await repo.delete(await repo.get(a.id))
        await uow.commit()
    async with store.unit_of_work() as uow:
        assert await uow.repository(CONTRACT_RESOURCE).get(a.id) is None
    with pytest.raises(TransactionTooLarge):
        async with store.unit_of_work() as uow:
            for n in range(101):
                await uow.repository(CONTRACT_RESOURCE).add(ContractItem(f"n{n}", n, "z"))
            await uow.commit()


async def test_taken_id_is_a_conflict(contract_store):
    async with contract_store.unit_of_work() as uow:
        await uow.repository(CONTRACT_RESOURCE).add(ContractItem("a", 1, id=7))
        await uow.commit()
    with pytest.raises(VersionConflict):
        async with contract_store.unit_of_work() as uow:
            await uow.repository(CONTRACT_RESOURCE).add(ContractItem("b", 2, id=7))
            await uow.commit()


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


async def test_types_round_trip(database):
    from resource_engine_azure import CosmosStore

    registry = ResourceRegistry()
    registry.register(PaymentResource)
    store = CosmosStore(database, registry, {"payments": await container(database)})
    payment = Payment(Decimal("12.5"), date(2026, 3, 1), datetime(2026, 3, 1, 9, tzinfo=UTC), ["a"])
    async with store.unit_of_work() as uow:
        await uow.repository("payments").add(payment)
        await uow.commit()
    async with store.unit_of_work() as uow:
        assert await uow.repository("payments").get(payment.id) == payment
        assert (await uow.repository("payments").list(ListQuery())).total == 1
