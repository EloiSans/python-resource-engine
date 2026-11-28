"""Versions of a resource's API and of its events, published side by side,
with deprecation notices.

**API versions.** A resource whose schemas change keeps serving the old ones
under the old version's prefix (``/v1/invoices`` next to ``/v2/invoices``).
Each older version converts its input to the current schemas and the current
output to its schemas; the operation itself (hooks, permissions, events) is
the same:

```python
class InvoiceResource(Resource):
    schemas = {"create": InvoiceInV2, "read": InvoiceOutV2}
    api_versions = {
        "v1": ApiVersion(
            read=InvoiceOutV1,
            create=InvoiceInV1,
            to_read=lambda out: {**out, "amount": str(out["total"])},
            from_create=lambda data: {**data, "total": data.pop("amount")},
            deprecation=Deprecation(since=date(2026, 12, 1), sunset=date(2027, 6, 30),
                                    link="https://docs.example.com/invoices/v2"),
        )
    }

mount_resources(app, registry, manager, versions=["v1", "v2"])
```

**Event versions.** An event whose payload changes declares
``schema_version = 2`` and keeps publishing the older shapes for consumers
not yet moved, each as its own message with ``schema_version`` set:

```python
@dataclass(frozen=True, kw_only=True)
class InvoiceApproved(DomainEvent):
    schema_version = 2
    previous_versions = {
        1: EventVersion(InvoiceApprovedV1, lambda p: {**p, "amount": str(p["total"])},
                        deprecation=Deprecation(sunset=date(2027, 6, 30))),
    }
    invoice_id: int
    total: Decimal
```

Consumers pick the version they are written for
(``router.on(InvoiceApproved, payload=..., schema_version=2)``); brokers can
also filter on the ``schema_version`` message attribute.

**Deprecation notices** go to the OpenAPI document (``deprecated``), the
responses (``Deprecation``, ``Sunset`` and ``Link`` headers, RFC 9745 and
8594), the AsyncAPI document and GraphQL (``@deprecated``). The contract
checks compare each version with the same version of the published document,
and let a version go only after its sunset.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import formatdate
from typing import Any

type Converter = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Deprecation:
    """A version, resource or event on its way out.

    Args:
        since: When it was deprecated (``None``: deprecated, no date).
        sunset: When it stops being served or published.
        link: Where the migration is explained.
        reason: One sentence for the documents (``"Use v2: totals are decimals."``).
    """

    since: date | None = None
    sunset: date | None = None
    link: str | None = None
    reason: str | None = None

    def headers(self) -> dict[str, str]:
        """``Deprecation``, ``Sunset`` and ``Link`` response headers."""
        headers = {"Deprecation": f"@{_epoch(self.since)}" if self.since else "true"}
        if self.sunset:
            headers["Sunset"] = formatdate(_epoch(self.sunset), usegmt=True)
        if self.link:
            headers["Link"] = f'<{self.link}>; rel="deprecation"'
        return headers

    def notice(self) -> str:
        """The deprecation in one sentence, for descriptions."""
        parts = ["Deprecated"]
        if self.since:
            parts.append(f"since {self.since.isoformat()}")
        text = " ".join(parts)
        if self.sunset:
            text += f"; removed on {self.sunset.isoformat()}"
        text += "."
        if self.reason:
            text += f" {self.reason}"
        if self.link:
            text += f" See {self.link}"
        return text

    def to_dict(self) -> dict[str, str]:
        data = {
            "since": self.since.isoformat() if self.since else None,
            "sunset": self.sunset.isoformat() if self.sunset else None,
            "link": self.link,
            "reason": self.reason,
        }
        return {k: v for k, v in data.items() if v is not None}

    def past_sunset(self, today: date | None = None) -> bool:
        return self.sunset is not None and (today or datetime.now(UTC).date()) >= self.sunset


def _epoch(day: date) -> int:
    return calendar.timegm(day.timetuple())


def from_dict(data: Mapping[str, Any] | None) -> Deprecation | None:
    """A ``Deprecation`` from ``to_dict`` output (documents)."""
    if not data:
        return None
    return Deprecation(
        since=date.fromisoformat(data["since"]) if data.get("since") else None,
        sunset=date.fromisoformat(data["sunset"]) if data.get("sunset") else None,
        link=data.get("link"),
        reason=data.get("reason"),
    )


@dataclass(frozen=True)
class ApiVersion:
    """An older version of a resource's API, served next to the current one.

    Args:
        read / create / update: That version's schemas (``None``: the current one).
        to_read: The current output (dumped to a dict) → that version's.
        from_create / from_update: That version's input (a dict) → the current one's.
        deprecation: Its deprecation notice.
    """

    read: Any = None
    create: Any = None
    update: Any = None
    to_read: Converter | None = None
    from_create: Converter | None = None
    from_update: Converter | None = None
    deprecation: Deprecation | None = None

    def schema(self, role: str, current: Mapping[str, Any]) -> Any:
        return getattr(self, role, None) or current.get(role)


@dataclass(frozen=True)
class EventVersion:
    """An older version of an event, still published next to the current one.

    Args:
        schema: Its payload type (the old event class or a model), for the
            AsyncAPI document and typed consumers.
        convert: The current payload (JSON-compatible dict) → this version's.
        deprecation: Its deprecation notice.
    """

    schema: Any
    convert: Converter
    deprecation: Deprecation | None = None


def event_version(event_cls: type) -> int:
    return int(getattr(event_cls, "schema_version", 1))


__all__ = [
    "ApiVersion",
    "Converter",
    "Deprecation",
    "EventVersion",
    "event_version",
    "from_dict",
]
