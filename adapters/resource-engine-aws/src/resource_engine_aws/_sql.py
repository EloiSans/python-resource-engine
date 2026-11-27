"""SQL literals for Athena's execution parameters (Trino syntax)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, Decimal):
        return f"DECIMAL '{value}'"
    if isinstance(value, datetime):
        moment = value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        return f"TIMESTAMP '{moment.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    text = str(value.value if hasattr(value, "value") else value)
    return "'" + text.replace("'", "''") + "'"
