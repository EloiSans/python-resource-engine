# Table export

Any resource that can be listed can be exported as CSV or XLSX, with the
same filters, sorting and permissions as the list:

```http
GET /invoices/export?format=xlsx&status=approved&order_by=-amount
```

```python
from resource_engine.exports import export_table

content, media_type, filename = await export_table(
    manager, "invoices", ListQuery(filters={"status": "approved"}), format="csv", actor=user
)
```

- **The caller's rows only:**
  - rows are read through `manager.list` as the caller, so the row filter
    and the tenant apply;
  - fields the caller may not read are left empty, though the column
    stays;
  - the permission is the list's own (`<resource>:list`).
- **Columns:** the read schema's fields, in order. Lists and objects
  become JSON text.
- **CSV:** UTF-8 with a byte order mark (so Excel opens accented text),
  comma-separated.
- **XLSX:** one sheet with a bold, frozen header. Numbers, decimals and
  dates stay native cells; datetimes are UTC, since Excel has no time
  zones. It needs `resource-engine[xlsx]` (openpyxl).
- **Size:** at most 10 000 rows (`max_rows`). A larger result is refused
  with RE-E103 and the total; with background exports set up, its hint
  points to them. The file is built in memory and sent with
  `Content-Disposition: attachment`.

The route is `GET /<resource>/export`, next to the list in OpenAPI, with
`format` (`csv` or `xlsx`), `order_by` and the list's filters. Front ends
can link to it directly with the user's token. In the explorer's console the
answer is a download link, not text.

## Background exports

Larger tables are exported in the background. The request is checked at once
(permission, filters, size) and answered with an id. A worker job writes the
file to the blob store, and the requester downloads it when it is ready:

```http
POST /invoices/exports?format=xlsx&status=approved        → 202, Location: /exports/4f0c…
GET  /exports/4f0c…                                       → {"status": "running", "rows": 48210, ...}
GET  /exports/4f0c…/content                               → the file, once "done"
```

```python
job = await manager.exports.request("invoices", query, format="xlsx", actor=user)
runner.add("exports", manager.exports, every=5, single=True)  # in the worker
```

- **Setup:** `manager.exports` exists whenever the manager has a blob store
  (`ResourceManager(blob_store=...)`), and then the routes are mounted. Add
  it to the worker's `JobRunner` with `single=True`; without a worker,
  requests stay `pending` and fail after `retention`.
- **Storage:** each export is two objects under `exports/` in the blob store:
  `<id>.json` (the request and its status) and `<id>.csv` or `<id>.xlsx`.
  There is no table to migrate.
- **Who sees it:** only the requester. Anyone else gets RE-E102, as if it
  did not exist. Rows are read as the requester, with the same row filter,
  tenant and hidden fields as the list. The worker uses the roles and claims
  they had when they asked: someone who loses access afterwards can still
  download the file until it expires.
- **States:** `pending`, `running`, `done` and `failed` (with `error`: its
  code and message). Downloading before `done` is RE-E605. A `running` export
  whose worker died runs again after `stale_after` (30 minutes), at most
  three times.
- **Size and time:** at most `max_rows` rows (1 000 000). CSV is streamed to
  the store; XLSX is written to a temporary file (on disk past 8 MB) and
  then streamed. Finished exports are deleted after `retention` (one day).
- **Downloads:** through the API, or with
  `mount_resources(redirect_downloads=True)` a 307 to a presigned URL when
  the store signs URLs.
- **Actors:** strings and `Principal`s are stored as they are. Other actor
  types need `ExportJobs(manager, store, dump_actor=..., load_actor=...)`,
  assigned to `manager.exports`.

Change the limits by replacing it:
`manager.exports = ExportJobs(manager, store, max_rows=200_000, retention=timedelta(hours=6))`.

## GraphQL and MCP

- **GraphQL:** with background exports set up, each listable resource gets
  `exportInvoices(format: CSV | XLSX, orderBy, filter)`, which returns an
  `Export` (`id status rows filename contentPath downloadUrl error`), and
  `export(id)` follows it. `downloadUrl` is a presigned URL when the store
  signs them; otherwise download `contentPath` over HTTP.
- **MCP:** `<resource>_export` takes `format`, `order_by` and `filters`. With
  background exports it queues one, and `<resource>_export_status` follows
  it. Without them the file comes back in the answer: CSV as text, XLSX in
  base64, up to 10 000 rows. Both tools are read-only and need the list
  permission.
