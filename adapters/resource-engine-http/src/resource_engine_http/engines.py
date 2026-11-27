"""Query engines over HTTP for data-lake view resources: Trino (and Presto
and Starburst, through Trino's REST protocol) and Google BigQuery.

```python
from resource_engine.querying import QueryEngineStore
from resource_engine_http import BigQueryEngine, TrinoEngine

trino = TrinoEngine(http, "https://trino:8443", user="invoices", catalog="lake",
                    max_scan_bytes=50 * 2**30)
bigquery = BigQueryEngine(http, project="acme", token=gcs_token(), maximum_bytes_billed=10 * 2**30)
```

Both run the SQL the ``Compiler`` writes, with its ``?`` values as
parameters: ``EXECUTE IMMEDIATE ... USING`` in Trino, typed positional query
parameters in BigQuery. Results come back with their types (decimals,
dates, timestamps).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from resource_engine.calls import outgoing_call
from resource_engine.errors import RemoteCallFailed
from resource_engine.querying import BIGQUERY, TRINO

# ------------------------------------------------------------------ Trino


def trino_literal(value: Any) -> str:
    """A value as a Trino SQL literal (for ``EXECUTE IMMEDIATE ... USING``)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return f"DECIMAL '{value}'"
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value
        text = moment.strftime("%Y-%m-%d %H:%M:%S.%f")
        return f"TIMESTAMP '{text} UTC'" if value.tzinfo else f"TIMESTAMP '{text}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    text = str(value.value if hasattr(value, "value") else value)
    return "'" + text.replace("'", "''") + "'"


def _trino_value(value: Any, kind: str) -> Any:
    if value is None:
        return None
    base = kind.split("(")[0]
    if base == "decimal":
        return Decimal(str(value))
    if base == "date":
        return date.fromisoformat(value)
    if base.startswith("timestamp"):
        text = str(value)
        if text.endswith(" UTC"):
            return datetime.fromisoformat(text[:-4]).replace(tzinfo=UTC)
        return datetime.fromisoformat(text)
    return value


class TrinoEngine:
    """``QueryEngine`` over Trino's REST protocol (``/v1/statement``).

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (TLS, authentication).
        url: The coordinator.
        user: ``X-Trino-User``.
        catalog / schema: Defaults for unqualified table names.
        max_scan_bytes: Session limit ``query_max_scan_physical_bytes``.
        timeout: Seconds before the query is cancelled.
    """

    dialect = TRINO
    service = "trino"

    def __init__(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        user: str = "resource-engine",
        catalog: str | None = None,
        schema: str | None = None,
        max_scan_bytes: int | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {"X-Trino-User": user}
        if catalog:
            self.headers["X-Trino-Catalog"] = catalog
        if schema:
            self.headers["X-Trino-Schema"] = schema
        session = [f"query_max_execution_time={int(timeout)}s"]
        if max_scan_bytes:
            session.append(f"query_max_scan_physical_bytes={int(max_scan_bytes)}B")
        self.headers["X-Trino-Session"] = ",".join(session)
        self.timeout = timeout

    def statement(self, sql: str, params: Sequence[Any]) -> str:
        if not params:
            return sql
        quoted = sql.replace("'", "''")
        return f"EXECUTE IMMEDIATE '{quoted}' USING " + ", ".join(trino_literal(p) for p in params)

    async def query(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self.timeout
        statement = self.statement(sql, params)
        try:
            async with outgoing_call(self.service, "query", method="POST", url=self.url) as call:
                response = await self.client.post(
                    f"{self.url}/v1/statement", content=statement, headers=self.headers
                )
                call.status = response.status_code
                response.raise_for_status()
                result = response.json()
                columns: list[dict[str, Any]] = []
                rows: list[dict[str, Any]] = []
                while True:
                    if result.get("error"):
                        error = result["error"]
                        raise RemoteCallFailed(
                            f"Trino: {error.get('message')}",
                            service=self.service,
                            operation="query",
                            body=error.get("errorName"),
                        )
                    columns = result.get("columns") or columns
                    for values in result.get("data") or []:
                        rows.append(
                            {
                                c["name"]: _trino_value(v, c.get("type", ""))
                                for c, v in zip(columns, values, strict=True)
                            }
                        )
                    following = result.get("nextUri")
                    if not following:
                        return rows
                    if time.monotonic() > deadline:
                        await self.client.delete(following, headers=self.headers)
                        raise RemoteCallFailed(
                            f"Trino query took longer than {self.timeout}s",
                            service=self.service,
                            operation="query",
                        )
                    page = await self.client.get(following, headers=self.headers)
                    page.raise_for_status()
                    result = page.json()
        except httpx.HTTPError as exc:
            raise RemoteCallFailed(
                f"Trino failed: {exc}", service=self.service, operation="query"
            ) from exc


# --------------------------------------------------------------- BigQuery


def _bigquery_parameter(value: Any) -> dict[str, Any]:
    if value is None:
        kind, text = "STRING", None
    elif isinstance(value, bool):
        kind, text = "BOOL", "true" if value else "false"
    elif isinstance(value, int):
        kind, text = "INT64", str(value)
    elif isinstance(value, float):
        kind, text = "FLOAT64", repr(value)
    elif isinstance(value, Decimal):
        kind, text = "NUMERIC", str(value)
    elif isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        kind, text = "TIMESTAMP", moment.isoformat()
    elif isinstance(value, date):
        kind, text = "DATE", value.isoformat()
    else:
        kind, text = "STRING", str(value.value if hasattr(value, "value") else value)
    return {"parameterType": {"type": kind}, "parameterValue": {"value": text}}


def _bigquery_value(value: Any, field: dict[str, Any]) -> Any:
    if value is None:
        return None
    kind = field.get("type")
    if field.get("mode") == "REPEATED":
        return [_bigquery_value(v["v"], {**field, "mode": "NULLABLE"}) for v in value]
    if kind in ("INTEGER", "INT64"):
        return int(value)
    if kind in ("FLOAT", "FLOAT64"):
        return float(value)
    if kind in ("NUMERIC", "BIGNUMERIC"):
        return Decimal(value)
    if kind in ("BOOLEAN", "BOOL"):
        return value == "true"
    if kind == "DATE":
        return date.fromisoformat(value)
    if kind == "TIMESTAMP":
        return datetime.fromtimestamp(float(value), UTC)
    if kind == "DATETIME":
        return datetime.fromisoformat(value)
    return value


class BigQueryEngine:
    """``QueryEngine`` over the BigQuery REST API (``jobs.query``).

    Args:
        client: Caller-owned ``httpx.AsyncClient``.
        project: The billing project.
        token: Async function returning a bearer token (``gcs_token()`` with
            the BigQuery scope); ``None`` for an emulator.
        location: The datasets' location (``EU``).
        maximum_bytes_billed: Queries that would bill more fail instead.
        timeout: Seconds to wait for the results.
        url: The API.
    """

    dialect = BIGQUERY
    service = "bigquery"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        project: str,
        token: Callable[[], Awaitable[str | None]] | None = None,
        location: str | None = None,
        maximum_bytes_billed: int | None = None,
        timeout: float = 60.0,
        url: str = "https://bigquery.googleapis.com",
    ) -> None:
        self.client = client
        self.project = project
        self.token = token
        self.location = location
        self.maximum_bytes_billed = maximum_bytes_billed
        self.timeout = timeout
        self.url = url.rstrip("/") + f"/bigquery/v2/projects/{project}"

    async def _headers(self) -> dict[str, str]:
        token = await self.token() if self.token else None
        return {"Authorization": f"Bearer {token}"} if token else {}

    def request(self, sql: str, params: Sequence[Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "POSITIONAL",
            "queryParameters": [_bigquery_parameter(p) for p in params],
            "timeoutMs": int(self.timeout * 1000),
        }
        if self.location:
            body["location"] = self.location
        if self.maximum_bytes_billed:
            body["maximumBytesBilled"] = str(self.maximum_bytes_billed)
        return body

    async def query(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self.timeout
        try:
            async with outgoing_call(self.service, "query", method="POST", url=self.url) as call:
                response = await self.client.post(
                    f"{self.url}/queries",
                    json=self.request(sql, params),
                    headers=await self._headers(),
                )
                call.status = response.status_code
                result = self._checked(response)
                job = result.get("jobReference", {})
                while not result.get("jobComplete", False):
                    if time.monotonic() > deadline:
                        raise RemoteCallFailed(
                            f"BigQuery query took longer than {self.timeout}s",
                            service=self.service,
                            operation="query",
                        )
                    await asyncio.sleep(0.5)
                    response = await self.client.get(
                        f"{self.url}/queries/{job.get('jobId')}",
                        params={"location": job.get("location") or self.location or ""},
                        headers=await self._headers(),
                    )
                    result = self._checked(response)
        except httpx.HTTPError as exc:
            raise RemoteCallFailed(
                f"BigQuery failed: {exc}", service=self.service, operation="query"
            ) from exc
        fields = result.get("schema", {}).get("fields", [])
        return [
            {
                f["name"]: _bigquery_value(cell.get("v"), f)
                for f, cell in zip(fields, row["f"], strict=True)
            }
            for row in result.get("rows", [])
        ]

    def _checked(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                message = response.text
            raise RemoteCallFailed(
                f"BigQuery: {message}",
                service=self.service,
                operation="query",
                status=response.status_code,
                body=message,
            )
        return response.json()


__all__ = ["BigQueryEngine", "TrinoEngine", "trino_literal"]
