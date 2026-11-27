"""Table export: a resource's list as CSV or XLSX, with the caller's filters,
sorting, permissions, hidden fields and tenant, exactly as the API lists it.

```python
from resource_engine.exports import export_table

content, media_type, filename = await export_table(
    manager, "invoices", ListQuery(filters={"status": "approved"}), format="xlsx", actor=user
)
```

Rows are read page by page through ``manager.list`` as the caller, so a user
exports what they could list and nothing else: rows outside their row filter
or tenant aren't there, and hidden fields are empty. ``max_rows`` (10 000 by
default) caps an export; above it, narrow the filters. Over HTTP:
``GET /<resource>/export?format=csv|xlsx`` with the list's filters
(``mount_resources``). XLSX needs ``resource-engine[xlsx]`` (openpyxl).
"""

from __future__ import annotations

import csv
import enum
import io
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from resource_engine.application.query import ListQuery
from resource_engine.errors import InvalidQuery

type ExportFormat = Literal["csv", "xlsx"]

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def columns_of(manager: Any, resource: str) -> list[str]:
    """The export's columns: the read schema's fields, in order."""
    definition = manager.registry.get(resource)
    read = definition.schemas.get("read")
    return list(read.model_fields) if read is not None else []


async def iter_rows(
    manager: Any,
    resource: str,
    query: ListQuery | None = None,
    *,
    actor: Any = None,
    tenant: str | None = None,
    page_size: int = 500,
    max_rows: int = 10_000,
) -> AsyncIterator[dict[str, Any]]:
    """The caller's rows as dicts (hidden fields left out), page by page."""
    from resource_engine.authz.fields import hidden_fields

    query = query or ListQuery()
    page_size = min(page_size, manager.registry.get(resource).max_page_size)
    offset, sent = 0, 0
    while True:
        page_query = ListQuery(
            offset=offset,
            limit=page_size,
            order_by=list(query.order_by),
            filters=dict(query.filters),
        )
        page = await manager.list(resource, page_query, actor=actor, tenant=tenant)
        if offset == 0 and page.total > max_rows:
            raise InvalidQuery(
                f"{page.total} {resource} match; an export is limited to {max_rows} rows",
                details={"total": page.total, "max_rows": max_rows},
                hint="Narrow the filters, or export in parts.",
            )
        for item in page.items:
            if isinstance(item, BaseModel):
                yield item.model_dump(exclude=set(hidden_fields(item)))
            else:
                yield dict(vars(item))
            sent += 1
        offset += page_size
        if len(page.items) < page_size or sent >= page.total:
            return


def _cell(value: Any, *, native: bool) -> Any:
    """A value for a cell: numbers and dates stay native in XLSX; lists and
    objects become JSON; ``None`` is empty."""
    if value is None:
        return "" if not native else None
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return value if native else ("true" if value else "false")
    if isinstance(value, int | float | Decimal):
        return value if native else str(value)
    if isinstance(value, datetime):
        if native:  # Excel has no time zones: UTC, without the offset
            return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        return value.isoformat()
    if isinstance(value, date):
        return value if native else value.isoformat()
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, default=str, ensure_ascii=False)


async def write_csv(
    rows: AsyncIterator[dict[str, Any]],
    columns: Sequence[str],
    *,
    delimiter: str = ",",
    excel: bool = True,
) -> bytes:
    """CSV with a header row. ``excel`` adds a UTF-8 byte order mark so Excel
    opens accented text correctly."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(columns)
    async for row in rows:
        writer.writerow([_cell(row.get(c), native=False) for c in columns])
    text = out.getvalue()
    return (("﻿" if excel else "") + text).encode("utf-8")


async def write_xlsx(
    rows: AsyncIterator[dict[str, Any]], columns: Sequence[str], *, sheet: str = "export"
) -> bytes:
    """An XLSX workbook (one sheet, header row frozen), written in openpyxl's
    streaming mode."""
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font
    except ImportError:  # pragma: no cover - depends on the extra
        raise InvalidQuery(
            "XLSX export needs openpyxl",
            hint="Install resource-engine[xlsx], or export as CSV.",
        ) from None
    book = Workbook(write_only=True)
    page = book.create_sheet(sheet[:31] or "export")
    page.freeze_panes = "A2"
    bold = Font(bold=True)
    header = []
    for name in columns:
        cell = WriteOnlyCell(page, value=name)
        cell.font = bold
        header.append(cell)
    page.append(header)
    async for row in rows:
        page.append([_cell(row.get(c), native=True) for c in columns])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


async def export_table(
    manager: Any,
    resource: str,
    query: ListQuery | None = None,
    *,
    format: ExportFormat = "csv",
    actor: Any = None,
    tenant: str | None = None,
    max_rows: int = 10_000,
    columns: Sequence[str] | None = None,
) -> tuple[bytes, str, str]:
    """``(content, media type, file name)`` of a resource's list."""
    if format not in MEDIA_TYPES:
        raise InvalidQuery(f"Unknown export format {format!r}; use csv or xlsx")
    names = list(columns or columns_of(manager, resource))
    rows = iter_rows(manager, resource, query, actor=actor, tenant=tenant, max_rows=max_rows)
    content = (
        await write_xlsx(rows, names, sheet=resource)
        if format == "xlsx"
        else await write_csv(rows, names)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return content, MEDIA_TYPES[format], f"{resource}-{stamp}.{format}"


__all__ = [
    "MEDIA_TYPES",
    "columns_of",
    "export_table",
    "iter_rows",
    "write_csv",
    "write_xlsx",
]
