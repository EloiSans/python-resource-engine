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
    seal: int | None = None  # sequence of the AuditSeal covering this entry


@dataclass
class AuditSeal:
    """A sealed batch of audit entries (see ``AuditSealer``).

    ``digest`` hashes the batch's entries; ``hash`` covers the digest, the
    batch's metadata and the previous seal's hash, so seals form a chain.
    ``mac`` is an HMAC of ``hash`` when the sealer has a key. ``purged_at`` is
    set when retention deleted the batch's entries; the seal itself stays.
    """

    sequence: int
    previous: str | None
    entries: int
    first_at: datetime
    last_at: datetime
    digest: str
    hash: str
    mac: str | None = None
    key_id: str | None = None
    sealed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    purged_at: datetime | None = None


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
    seal: int | None = Field(default=None, description="Seal covering the entry; null until sealed")


class AuditSealRead(BaseModel):
    """A sealed batch of audit entries."""

    model_config = ConfigDict(from_attributes=True)

    sequence: int = Field(description="Position in the seal chain, from 1")
    previous: str | None = Field(default=None, description="Hash of the previous seal")
    entries: int = Field(description="Number of entries in the batch")
    first_at: datetime = Field(description="Time of the oldest entry")
    last_at: datetime = Field(description="Time of the newest entry")
    digest: str = Field(description="SHA-256 of the batch's entries")
    hash: str = Field(description="SHA-256 of the seal, chained to the previous one")
    mac: str | None = Field(default=None, description="HMAC-SHA-256 of the hash, when keyed")
    key_id: str | None = Field(default=None, description="Key that produced the HMAC")
    sealed_at: datetime = Field(description="When the batch was sealed")
    purged_at: datetime | None = Field(
        default=None, description="When retention deleted the entries"
    )
