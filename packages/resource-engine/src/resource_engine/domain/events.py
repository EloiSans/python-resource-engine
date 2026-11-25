from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Something that happened in the domain.

    Events are collected during an operation and published only after the
    transaction commits. Subclass it as a frozen dataclass::

        @dataclass(frozen=True, kw_only=True)
        class InvoiceApproved(DomainEvent):
            invoice_id: int
    """

    occurred_at: datetime = field(default_factory=_now)

    @property
    def name(self) -> str:
        return type(self).__name__


def event_doc(event_cls: type) -> str | None:
    """The event class's own docstring; ``None`` for the signature that
    ``@dataclass`` writes when there is none."""
    doc = event_cls.__dict__.get("__doc__")
    if not doc or doc.startswith(f"{event_cls.__name__}("):
        return None
    return doc
