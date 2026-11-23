"""SQLAlchemy mapping of the invoices domain. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from resource_engine_sqlalchemy import authz_tables, documents_table, map_authz, map_documents
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, Text
from sqlalchemy.orm import registry as orm_registry

from invoices.domain.entities import Invoice

metadata = MetaData()
mapper_registry = orm_registry(metadata=metadata)

invoices_table = Table(
    "invoices",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer", String(200), nullable=False),
    Column("amount", Numeric(12, 2, asdecimal=True), nullable=False),
    Column("issue_date", Date, nullable=True),
    Column("notes", Text, nullable=True),
    Column("status", String(32), nullable=False),
    Column("rejection_reason", Text, nullable=True),
)

documents = documents_table(metadata)
authz = authz_tables(metadata)

_mapped = False


def map_entities() -> None:
    """Idempotently map domain entities to tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively(Invoice, invoices_table)
        map_documents(mapper_registry, documents)
        map_authz(mapper_registry, authz)
        _mapped = True
