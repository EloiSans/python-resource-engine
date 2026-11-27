# Data-lake views (query engines)

Tables in a data lake (Parquet or Iceberg files on S3, GCS or ADLS, queried
by Athena, BigQuery or Trino) can be served as read-only resources. They get
the usual filters, sorting, paging, row-level permissions, tenancy, `expand`,
the API, GraphQL, MCP tools and the generated docs, like any view.

```python
from resource_engine.querying import QueryEngineStore
from resource_engine_http import TrinoEngine


class DailySalesResource(Resource):
    """Sales per day and region, from the data lake."""

    name = "daily_sales"
    model = DailySale
    schemas = {"read": DailySaleRead}
    operations = ["list", "get"]
    filterable = ["region"]
    sortable = ["day", "amount"]
    tenant_field = "org"


store = QueryEngineStore(
    TrinoEngine(http, "https://trino:8443", user="invoices", max_scan_bytes=50 * 2**30),
    registry,
    tables={"daily_sales": "lake.sales.daily"},
)
manager = ResourceManager(registry, store.unit_of_work)
```

- **One query per page, plus a count.** Filters, the caller's row filter and
  the tenant become `WHERE`, followed by `ORDER BY` and the engine's paging.
  Values are always parameters, never pasted into the SQL. Identifiers are
  checked and quoted.
- **Supported conditions:** `== != < <= > >=`, `in`, `nin`, `exists`,
  `contains` (array columns), `and`, `or` and `not`. Anything else, including
  related filters (`customer.country`), is refused with RE-E705 rather than
  guessed.
- **Read-only.** Writes raise RE-E003. Declare `operations = ["list", "get"]`.
- **Cost:** each engine takes a limit, and a query over it fails with
  RE-E501 instead of running up the bill. Results can also be cached with
  `cache_ttl` and the `Cache` port ([caching](caching.md)).
- **With SQL resources:** an application whose other resources live in
  PostgreSQL combines both stores with a composite unit of work
  ([composite](composite.md)).

| Engine | Package | Class | Limit | Tested against |
|---|---|---|---|---|
| Trino, Presto, Starburst | `resource-engine-http` | `TrinoEngine(client, url, user=, catalog=, schema=)` | `max_scan_bytes` (session property), `timeout` | Trino 476 (compose, memory catalog) |
| Amazon Athena | `resource-engine-aws[athena]` | `AthenaEngine(client, database=, workgroup=)` | the workgroup's bytes-scanned cutoff, `timeout` | fake client |
| Google BigQuery | `resource-engine-http` | `BigQueryEngine(client, project=, token=)` | `maximum_bytes_billed`, `timeout` | fake API |
| Azure Synapse serverless, Databricks SQL, Snowflake | `resource-engine-sqlalchemy` | `map_view` with their SQLAlchemy dialect | the engine's own | – |

Synapse serverless (T-SQL over ODBC), Databricks SQL and Snowflake have
SQLAlchemy dialects. The SQL adapter's [custom views](views.md) serve them
with the same features, so they need no adapter of their own.
