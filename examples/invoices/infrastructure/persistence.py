"""SQLAlchemy mapping of the invoices domain. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables, map_engine
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, Text
from sqlalchemy.orm import registry as orm_registry

from invoices.domain.entities import Invoice

# One MetaData for the module's tables and the engine's: Alembic autogenerate
# sees both (examples/invoices/migrations). The convention names constraints.
metadata = MetaData(naming_convention=NAMING_CONVENTION)
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
    Column("version", Integer, nullable=False, default=1),
)

engine = engine_tables(metadata, documents=True, authz=True, audit=True, idempotency=True)

_mapped = False


def map_entities() -> None:
    """Idempotently map domain entities to tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively(Invoice, invoices_table)
        map_engine(mapper_registry, engine)
        _mapped = True
