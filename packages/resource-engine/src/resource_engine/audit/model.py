"""Audit entries and their read schema."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class AuditEntry:
    """Who did what to which record, and whether it was allowed."""

    resource: str
    operation: str
    action: str
    outcome: str  # ok | denied
    actor: str | None = None
    actor_roles: list[str] = field(default_factory=list)
    entity_id: str | None = None
    tenant: str | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class AuditEntryRead(BaseModel):
    """An audit entry."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Entry id")
    at: datetime = Field(description="When it happened (UTC)")
    actor: str | None = Field(
        default=None, description="Principal id, system:<job> or null (anonymous)"
    )
    actor_roles: list[str] = Field(description="Application roles of the actor at the time")
    resource: str = Field(description="Resource name")
    entity_id: str | None = Field(default=None, description="Target record")
    tenant: str | None = Field(default=None, description="Tenant of the record or operation")
    operation: str = Field(description="create, update, delete, transition, action, attach...")
    action: str = Field(description="Permission action, e.g. transition:approve")
    outcome: str = Field(description="ok or denied")
    changes: dict[str, Any] = Field(
        description="field → {from, to}; masked fields → {changed: true}"
    )
    details: dict[str, Any] = Field(description="Transition, input, documents, denial reason")
    correlation_id: str | None = Field(default=None, description="Shared with logs and events")
