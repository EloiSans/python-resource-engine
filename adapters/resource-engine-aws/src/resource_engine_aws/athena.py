"""Amazon Athena as a ``QueryEngine`` for data-lake view resources (extra
``[athena]``).

```python
async with session.create_client("athena", region_name="eu-west-1") as athena:
    engine = AthenaEngine(athena, database="sales", workgroup="invoices",
                          output_location="s3://acme-athena-results/")
```

Queries run with ``ExecutionParameters`` (the ``?`` values as SQL literals),
are polled until they finish (or ``timeout``) and their results read page by
page, typed from the result metadata. Limit the bytes scanned per query in
the workgroup (``BytesScannedCutoffPerQuery``): Athena cancels a query over
it, and the error comes back as ``RemoteCallFailed``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from resource_engine.errors import RemoteCallFailed
from resource_engine.querying import TRINO


def athena_literal(value: Any) -> str:
    """A ``?`` value as the SQL text Athena's execution parameters take."""
    from resource_engine_aws._sql import literal

    return literal(value)


def _value(text: str | None, kind: str) -> Any:
    if text is None:
        return None
    if kind in ("integer", "bigint", "smallint", "tinyint"):
        return int(text)
    if kind in ("double", "float", "real"):
        return float(text)
    if kind == "decimal":
        return Decimal(text)
    if kind == "boolean":
        return text == "true"
    if kind == "date":
        return date.fromisoformat(text)
    if kind.startswith("timestamp"):
        return datetime.fromisoformat(text)
    return text


class AthenaEngine:
    """``QueryEngine`` over Athena.

    Args:
        client: An aiobotocore ``athena`` client (owned by the caller).
        database: The Glue database for unqualified table names.
        workgroup: The workgroup (its limits and result location apply).
        output_location: Where results go, when the workgroup doesn't set it.
        timeout: Seconds before the query is stopped.
        poll: Seconds between status checks.
    """

    dialect = TRINO
    service = "athena"

    def __init__(
        self,
        client: Any,
        *,
        database: str | None = None,
        workgroup: str = "primary",
        output_location: str | None = None,
        timeout: float = 60.0,
        poll: float = 0.5,
    ) -> None:
        self.client = client
        self.database = database
        self.workgroup = workgroup
        self.output_location = output_location
        self.timeout = timeout
        self.poll = poll

    def _failed(self, message: str, exc: Exception | None = None) -> RemoteCallFailed:
        return RemoteCallFailed(f"Athena: {message}", service=self.service, operation="query")

    async def query(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        request: dict[str, Any] = {"QueryString": sql, "WorkGroup": self.workgroup}
        if params:
            request["ExecutionParameters"] = [athena_literal(p) for p in params]
        if self.database:
            request["QueryExecutionContext"] = {"Database": self.database}
        if self.output_location:
            request["ResultConfiguration"] = {"OutputLocation": self.output_location}
        try:
            started = await self.client.start_query_execution(**request)
            query_id = started["QueryExecutionId"]
            deadline = time.monotonic() + self.timeout
            while True:
                status = (await self.client.get_query_execution(QueryExecutionId=query_id))[
                    "QueryExecution"
                ]["Status"]
                state = status["State"]
                if state == "SUCCEEDED":
                    break
                if state in ("FAILED", "CANCELLED"):
                    raise self._failed(status.get("StateChangeReason", state))
                if time.monotonic() > deadline:
                    await self.client.stop_query_execution(QueryExecutionId=query_id)
                    raise self._failed(f"the query took longer than {self.timeout}s")
                await asyncio.sleep(self.poll)
            rows: list[dict[str, Any]] = []
            token: str | None = None
            columns: list[tuple[str, str]] = []
            first = True
            while True:
                page_request: dict[str, Any] = {"QueryExecutionId": query_id, "MaxResults": 1000}
                if token:
                    page_request["NextToken"] = token
                page = await self.client.get_query_results(**page_request)
                result = page["ResultSet"]
                if not columns:
                    columns = [
                        (c["Name"], c["Type"]) for c in result["ResultSetMetadata"]["ColumnInfo"]
                    ]
                data = result.get("Rows", [])
                if first:
                    data = data[1:]  # the header row
                    first = False
                for row in data:
                    values = [cell.get("VarCharValue") for cell in row["Data"]]
                    rows.append(
                        {
                            name: _value(v, kind)
                            for (name, kind), v in zip(columns, values, strict=True)
                        }
                    )
                token = page.get("NextToken")
                if not token:
                    return rows
        except RemoteCallFailed:
            raise
        except Exception as exc:  # the SDK's error hierarchy
            raise self._failed(str(exc), exc) from exc


__all__ = ["AthenaEngine", "athena_literal"]
