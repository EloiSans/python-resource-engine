"""Table and imperative mapping for the audit trail."""

from __future__ import annotations

from resource_engine.audit import AuditEntry
from sqlalchemy import JSON, Column, DateTime, Index, MetaData, String, Table
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import registry as orm_registry


def audit_table(metadata: MetaData, name: str = "resource_engine_audit_log") -> Table:
    """Declare the audit table on your metadata (create it with migrations)."""
    return Table(
        name,
        metadata,
        Column("id", String(32), primary_key=True),
        Column("at", DateTime(timezone=True), nullable=False, index=True),
        Column("actor", String(300), index=True),
        Column("actor_roles", JSON, nullable=False),
        Column("resource", String(100), nullable=False),
        Column("entity_id", String(200)),
        Column("operation", String(50), nullable=False),
        Column("action", String(200), nullable=False),
        Column("outcome", String(10), nullable=False),
        Column("changes", JSON, nullable=False),
        Column("details", JSON, nullable=False),
        Column("correlation_id", String(64), index=True),
        Index(f"ix_{name}_record", "resource", "entity_id"),
    )


def map_audit(registry: orm_registry, table: Table) -> None:
    """Map ``AuditEntry`` (once per process; later calls are ignored)."""
    if sa_inspect(AuditEntry, raiseerr=False) is None:
        registry.map_imperatively(AuditEntry, table)
