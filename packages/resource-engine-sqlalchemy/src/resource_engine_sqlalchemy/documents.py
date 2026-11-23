"""Table and imperative mapping for the built-in ``Document`` entity."""

from __future__ import annotations

from resource_engine.documents import Document
from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, MetaData, String, Table, Text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import registry as orm_registry

DEFAULT_TABLE = "resource_engine_documents"


def documents_table(metadata: MetaData, name: str = DEFAULT_TABLE) -> Table:
    """Declare the documents table on your metadata (and create it with your migrations)."""
    return Table(
        name,
        metadata,
        Column("id", String(32), primary_key=True),
        Column("owner_resource", String(200), nullable=False),
        Column("owner_id", String(200), nullable=False),
        Column("slot", String(100), nullable=False),
        Column("status", String(20), nullable=False, index=True),
        Column("filename", String(255), nullable=False),
        Column("content_type", String(255), nullable=False),
        Column("storage_key", String(1024), nullable=False),
        Column("size", BigInteger, nullable=True),
        Column("sha256", String(64), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=True),
        Column("created_by", String(200), nullable=True),
        Column("expires_at", DateTime(timezone=True), nullable=True),
        Column("metadata", JSON, nullable=False, default=dict),
        Column("error", Text, nullable=True),
        Column("purged_at", DateTime(timezone=True), nullable=True),
        Index(f"ix_{name}_owner", "owner_resource", "owner_id", "slot"),
    )


def map_documents(registry: orm_registry, table: Table) -> None:
    """Map ``Document`` onto ``table`` (once per process; later calls are ignored)."""
    if sa_inspect(Document, raiseerr=False) is not None:
        return
    registry.map_imperatively(Document, table)
