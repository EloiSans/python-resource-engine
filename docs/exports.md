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
  with RE-E103 and the total, so narrow the filters. The file is built in
  memory and sent with `Content-Disposition: attachment`.

The route is `GET /<resource>/export`, next to the list in OpenAPI, with
`format` (`csv` or `xlsx`), `order_by` and the list's filters. Front ends
can link to it directly with the user's token, and the explorer's console
offers it like any other operation.
