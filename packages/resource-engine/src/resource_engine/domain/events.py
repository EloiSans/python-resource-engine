from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar


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

    When its payload changes in a way consumers notice, raise
    ``schema_version`` and keep publishing the older shapes in
    ``previous_versions`` (``resource_engine.versions.EventVersion``) until
    their sunset. ``deprecation`` marks the event itself as on its way out.
    """

    schema_version: ClassVar[int] = 1
    previous_versions: ClassVar[Mapping[int, Any]] = {}
    deprecation: ClassVar[Any] = None

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
