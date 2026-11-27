"""Query-engine resources: conditions compiled to SQL with parameters."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from resource_engine import ListQuery
from resource_engine.errors import ConditionNotSupported, OperationNotEnabled
from resource_engine.querying import BIGQUERY, TRINO, Compiler, QueryEngineRepository


def test_conditions_become_parameterised_sql():
    compiler = Compiler(TRINO)
    where = compiler.condition(
        {
            "and": [
                {"eq": [{"attr": "resource.region"}, "EU"]},
                {"or": [
                    {"ge": [{"attr": "resource.amount"}, 100]},
                    {"in": [{"attr": "resource.status"}, ["open", "late"]]},
                ]},
                {"not": {"eq": [{"attr": "resource.owner"}, None]}},
                {"contains": [{"attr": "resource.tags"}, "vip"]},
            ]
        }
    )  # fmt: skip
    assert where == (
        '("region" = ? AND ("amount" >= ? OR "status" IN (?, ?)) AND (NOT "owner" IS NULL)'
        ' AND contains("tags", ?))'
    )
    assert compiler.params == ["EU", 100, "open", "late", "vip"]
    bq = Compiler(BIGQUERY)
    assert bq.condition({"contains": [{"attr": "resource.tags"}, "x"]}) == "? IN UNNEST(`tags`)"
    assert (
        BIGQUERY.page(20, 10) == " LIMIT 10 OFFSET 20"
        and TRINO.page(20, 10) == " OFFSET 20 LIMIT 10"
    )
    with pytest.raises(ConditionNotSupported):
        Compiler(TRINO).condition({"eq": [{"attr": "subject.id"}, "x"]})
    with pytest.raises(ConditionNotSupported):
        TRINO.identifier('a"; DROP TABLE x; --')


@dataclass
class Sale:
    region: str
    amount: int
    id: int | None = None


class FakeEngine:
    dialect = TRINO

    def __init__(self) -> None:
        self.calls = []

    async def query(self, sql, params):
        self.calls.append((sql, list(params)))
        if sql.startswith("SELECT COUNT"):
            return [{"total": 3}]
        return [{"region": "EU", "amount": 5, "id": 1}]


async def test_repository_lists_with_one_count_and_one_page():
    engine = FakeEngine()
    repo = QueryEngineRepository(engine, "lake.sales", Sale)
    page = await repo.list(
        ListQuery(offset=10, limit=5, filters={"region": "EU"}, order_by=["-amount"],
                  condition={"gt": [{"attr": "resource.amount"}, 1]})
    )  # fmt: skip
    assert page.total == 3 and page.items == [Sale("EU", 5, 1)]
    count, select = engine.calls
    assert count == (
        'SELECT COUNT(*) AS total FROM "lake"."sales" WHERE "region" = ? AND "amount" > ?',
        ["EU", 1],
    )
    assert select[0].endswith('ORDER BY "amount" DESC OFFSET 10 LIMIT 5')
    assert await repo.get(1) == Sale("EU", 5, 1)
    with pytest.raises(OperationNotEnabled):
        await repo.add(Sale("EU", 1))
