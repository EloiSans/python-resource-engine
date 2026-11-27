"""TrinoEngine against Trino (compose, :8088, memory catalog) through the
manager, and BigQueryEngine against a fake API."""

from __future__ import annotations

import json
import socket
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.ports.identity import Principal
from resource_engine.querying import QueryEngineStore
from resource_engine_http import BigQueryEngine, TrinoEngine

TRINO_URL = "http://127.0.0.1:8088"


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8088), timeout=1):
            return True
    except OSError:
        return False


def trino(test):
    test = pytest.mark.emulator("trino")(test)
    if not _up():
        test = pytest.mark.skip(reason="Trino not reachable on 127.0.0.1:8088")(test)
    return test


@dataclass
class Sale:
    region: str
    amount: Decimal
    day: date
    org: str
    id: int | None = None


class SaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    region: str
    amount: Decimal
    day: date
    org: str


@trino
async def test_a_lake_table_as_a_tenant_scoped_view():
    table = f"memory.default.sales_{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(timeout=60) as http:
        engine = TrinoEngine(http, TRINO_URL)
        await engine.query(
            f"CREATE TABLE {table} AS SELECT * FROM (VALUES "
            "(1, 'EU', DECIMAL '10.50', DATE '2026-01-01', 'acme'),"
            "(2, 'US', DECIMAL '20.00', DATE '2026-01-02', 'acme'),"
            "(3, 'EU', DECIMAL '5.25', DATE '2026-01-03', 'acme'),"
            "(4, 'EU', DECIMAL '99.00', DATE '2026-01-04', 'globex'))"
            " AS t(id, region, amount, day, org)",
            [],
        )

        class SaleResource(Resource):
            name = "sales"
            model = Sale
            schemas = {"read": SaleRead}
            operations = ["list", "get"]
            filterable = ["region"]
            sortable = ["amount", "day"]
            tenant_field = "org"

        registry = ResourceRegistry()
        registry.register(SaleResource)
        store = QueryEngineStore(engine, registry, {"sales": table})
        manager = ResourceManager(registry, store.unit_of_work)
        acme = Principal(subject="a", issuer="i", tenant="acme")
        page = await manager.list(
            "sales", ListQuery(filters={"region": "EU"}, order_by=["-amount"]), actor=acme
        )
        assert page.total == 2  # globex's EU sale is another tenant's
        assert [s.amount for s in page.items] == [Decimal("10.50"), Decimal("5.25")]
        assert page.items[0].day == date(2026, 1, 1)
        second = await manager.list(
            "sales", ListQuery(limit=1, offset=1, order_by=["day"]), actor=acme
        )
        assert [s.id for s in second.items] == [2] and second.total == 3
        assert (await manager.get("sales", 3, actor=acme)).region == "EU"
        await engine.query(f"DROP TABLE {table}", [])


async def test_bigquery_parameters_and_typed_rows():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                200, json={"jobComplete": False, "jobReference": {"jobId": "j1", "location": "EU"}}
            )
        return httpx.Response(200, json={
            "jobComplete": True,
            "schema": {"fields": [
                {"name": "id", "type": "INTEGER"}, {"name": "amount", "type": "NUMERIC"},
                {"name": "day", "type": "DATE"},
                {"name": "tags", "type": "STRING", "mode": "REPEATED"},
            ]},
            "rows": [{"f": [{"v": "1"}, {"v": "10.50"}, {"v": "2026-01-01"}, {"v": [{"v": "a"}]}]}],
        })  # fmt: skip

    engine = BigQueryEngine(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        project="acme",
        maximum_bytes_billed=10**9,
    )
    rows = await engine.query("SELECT 1 FROM `d.t` WHERE x = ? AND y = ?", ["EU", Decimal("1.5")])
    assert rows == [{"id": 1, "amount": Decimal("10.50"), "day": date(2026, 1, 1), "tags": ["a"]}]
    body = json.loads(seen[0].content)
    assert body["parameterMode"] == "POSITIONAL" and body["maximumBytesBilled"] == "1000000000"
    assert body["queryParameters"][1] == {
        "parameterType": {"type": "NUMERIC"}, "parameterValue": {"value": "1.5"},
    }  # fmt: skip
    assert seen[1].url.path.endswith("/queries/j1")

    from resource_engine.errors import RemoteCallFailed

    failing = BigQueryEngine(
        httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"message": "bytes billed exceeded"}}))),
        project="acme",
    )  # fmt: skip
    with pytest.raises(RemoteCallFailed, match="bytes billed"):
        await failing.query("SELECT 1", [])
