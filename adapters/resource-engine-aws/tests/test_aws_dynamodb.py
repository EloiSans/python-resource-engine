"""DynamoDB repositories against the AWS Emulator: the repository, condition
and version contracts, one transaction per commit, indexes and the list bound."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from resource_engine import ListQuery, Resource, ResourceRegistry
from resource_engine.errors import QueryNotSupported, TransactionTooLarge
from resource_engine.testing.contracts import (
    CONTRACT_RESOURCE,
    CONTRACT_VERSIONED,
    ConditionContract,
    ContractItem,
    VersionContract,
    contract_registry,
)
from resource_engine_aws.dynamodb import DynamoDbStore, DynamoTable, from_item, to_item

from .conftest import CA, ENDPOINT

pytestmark = pytest.mark.emulator("aws")


def _client():
    return get_session().create_client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="emulator",
        aws_secret_access_key="emulator",
        verify=CA,
        config=AioConfig(inject_host_prefix=False),
    )


@pytest.fixture
async def dynamodb():
    async with _client() as client:
        yield client


async def create_table(client, *, key="id", key_type="N", indexes=()):
    name = f"re-{uuid.uuid4().hex[:12]}"
    attributes = [{"AttributeName": key, "AttributeType": key_type}]
    request = {
        "TableName": name,
        "KeySchema": [{"AttributeName": key, "KeyType": "HASH"}],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if indexes:
        request["GlobalSecondaryIndexes"] = [
            {
                "IndexName": index,
                "KeySchema": [{"AttributeName": field, "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
            for field, index in indexes
        ]
        attributes += [{"AttributeName": f, "AttributeType": "S"} for f, _ in indexes]
    await client.create_table(AttributeDefinitions=attributes, **request)
    await client.get_waiter("table_exists").wait(
        TableName=name, WaiterConfig={"Delay": 0.2, "MaxAttempts": 100}
    )
    return name


@pytest.fixture(scope="module")
def tables():
    """Created once per module (slow in the emulator), emptied before each test."""

    async def create():
        async with _client() as client:
            return {
                CONTRACT_RESOURCE: await create_table(client, indexes=[("group", "by-group")]),
                CONTRACT_VERSIONED: await create_table(client),
                "payments": await create_table(client, key_type="S"),
            }

    return asyncio.run(create())


@pytest.fixture
async def empty(dynamodb, tables):
    for name in tables.values():
        key = "id"
        for item in (await dynamodb.scan(TableName=name, ProjectionExpression=key))["Items"]:
            await dynamodb.delete_item(TableName=name, Key={key: item[key]})
    return tables


@pytest.fixture
async def contract_store(dynamodb, empty):
    return DynamoDbStore(
        dynamodb,
        contract_registry(),
        {
            CONTRACT_RESOURCE: DynamoTable(empty[CONTRACT_RESOURCE], indexes={"group": "by-group"}),
            CONTRACT_VERSIONED: empty[CONTRACT_VERSIONED],
        },
    )


class TestDynamoDbRepository(ConditionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


class TestDynamoDbVersions(VersionContract):
    @pytest.fixture
    def contract_uow_factory(self, contract_store):
        return contract_store.unit_of_work


async def test_one_transaction_per_commit(contract_store, dynamodb):
    async with contract_store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        kept = await repo.add(ContractItem("kept", 1))
        dropped = await repo.add(ContractItem("dropped", 2))
        await repo.delete(dropped)  # created and deleted: never sent
        assert (await repo.get(kept.id)).name == "kept"  # reads see the pending write
        await uow.commit()
    table = contract_store.tables[CONTRACT_RESOURCE].name
    assert (await dynamodb.scan(TableName=table, Select="COUNT"))["Count"] == 1

    with pytest.raises(TransactionTooLarge):
        async with contract_store.unit_of_work() as uow:
            for n in range(101):
                await uow.repository(CONTRACT_RESOURCE).add(ContractItem(f"n{n}", n))
            await uow.commit()
    assert (await dynamodb.scan(TableName=table, Select="COUNT"))["Count"] == 1


async def test_lists_use_the_key_and_indexes_and_are_bounded(contract_store, dynamodb):
    async with contract_store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        first = await repo.add(ContractItem("a", 1, "x"))
        for n in range(4):
            await repo.add(ContractItem(f"b{n}", n, "y"))
        await uow.commit()
    calls = []
    original = dynamodb.query

    async def spy(**kw):
        calls.append(kw.get("IndexName"))
        return await original(**kw)

    dynamodb.query = spy
    async with contract_store.unit_of_work() as uow:
        repo = uow.repository(CONTRACT_RESOURCE)
        page = await repo.list(ListQuery(filters={"group": "y"}, order_by=["-rank"], limit=2))
        assert [i.name for i in page.items] == ["b3", "b2"] and page.total == 4
        assert calls == ["by-group"]
        assert [i.name for i in (await repo.list(ListQuery(filters={"id": first.id}))).items] == [
            "a"
        ]
        repo.max_items = 3
        with pytest.raises(QueryNotSupported):
            await repo.list(ListQuery())


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


async def test_types_round_trip(dynamodb, empty):
    registry = ResourceRegistry()
    registry.register(PaymentResource)
    store = DynamoDbStore(dynamodb, registry, {"payments": empty["payments"]})
    payment = Payment(
        Decimal("12.50"), date(2026, 3, 1), datetime(2026, 3, 1, 9, tzinfo=UTC), ["a"]
    )
    async with store.unit_of_work() as uow:
        await uow.repository("payments").add(payment)
        await uow.commit()
    async with store.unit_of_work() as uow:
        assert await uow.repository("payments").get(payment.id) == payment
    assert from_item(to_item({"n": 3, "d": Decimal("1.5"), "x": None})) == {
        "n": 3,
        "d": Decimal("1.5"),
        "x": None,
    }
