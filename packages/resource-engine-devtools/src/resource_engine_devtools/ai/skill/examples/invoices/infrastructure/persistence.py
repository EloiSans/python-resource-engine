"""SQLAlchemy mapping of the invoices domain. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables, map_engine, map_views
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import registry as orm_registry

from invoices.domain.entities import Customer, Invoice, InvoicePayment, InvoiceTag, Tag

# One MetaData for the module's tables and the engine's: Alembic autogenerate
# sees both (examples/invoices/migrations). The convention names constraints.
metadata = MetaData(naming_convention=NAMING_CONVENTION)
mapper_registry = orm_registry(metadata=metadata)

customers_table = Table(
    "customers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("tax_id", String(20), nullable=False),
    Column("country", String(2), nullable=False),
    Column("email", String(320), nullable=True),
    Column("organization", String(100), nullable=True, index=True),
    UniqueConstraint("organization", "tax_id"),
)

invoices_table = Table(
    "invoices",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer", String(200), nullable=False),
    Column("amount", Numeric(12, 2, asdecimal=True), nullable=False),
    Column("issue_date", Date, nullable=True),
    # the engine checks the reference (RE-E108, RE-E109); the key backs it up
    Column("customer_id", Integer, ForeignKey("customers.id"), nullable=True, index=True),
    Column("notes", Text, nullable=True),
    Column("status", String(32), nullable=False),
    Column("rejection_reason", Text, nullable=True),
    Column("version", Integer, nullable=False, default=1),
    Column("status_changed_at", DateTime(timezone=True), nullable=True, index=True),
    Column("delivery_status", String(32), nullable=False, server_default="unsent"),
    # tenant (organization) when the app runs tenant-scoped; every query filters by it
    Column("organization", String(100), nullable=True, index=True),
)

tags_table = Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(50), nullable=False),
    Column("color", String(20), nullable=False),
    Column("organization", String(100), nullable=True, index=True),
    UniqueConstraint("organization", "name"),
)

invoice_tags_table = Table(
    "invoice_tags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("invoice_id", Integer, ForeignKey("invoices.id"), nullable=False, index=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), nullable=False, index=True),
    Column("organization", String(100), nullable=True, index=True),
    UniqueConstraint("invoice_id", "tag_id"),
)

invoice_payments_table = Table(
    "invoice_payments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # one process instance per invoice (the correlation key)
    Column("invoice_id", Integer, ForeignKey("invoices.id"), nullable=False, unique=True),
    Column("amount", Numeric(12, 2, asdecimal=True), nullable=False),
    Column("status", String(32), nullable=False),
    Column("status_changed_at", DateTime(timezone=True), nullable=True, index=True),
    Column("organization", String(100), nullable=True, index=True),
)

engine = engine_tables(
    metadata,
    documents=True,
    authz=True,
    api_keys=True,
    audit=True,
    idempotency=True,
    outbox=True,  # events stored with each change, then fed to processes and brokers
    inbox=True,  # messages already handled (exactly-once processes)
    locks=True,  # one worker at a time for single jobs
    activity=True,  # what the worker did, for the explorer (jobs, runs, deliveries)
)

_mapped = False


def map_entities() -> None:
    """Idempotently map domain entities to tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively(Customer, customers_table)
        mapper_registry.map_imperatively(Invoice, invoices_table)
        mapper_registry.map_imperatively(Tag, tags_table)
        mapper_registry.map_imperatively(InvoiceTag, invoice_tags_table)
        mapper_registry.map_imperatively(InvoicePayment, invoice_payments_table)
        # views (customer_balances): mapped onto their GROUP BY query, no table
        from invoices.bootstrap import registry

        map_views(mapper_registry, registry)
        map_engine(mapper_registry, engine)
        _mapped = True
