"""SQLAlchemy mapping of the customers module. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.orm import registry as orm_registry

from helpdesk.customers.domain.entities import Customer

metadata = MetaData()
mapper_registry = orm_registry(metadata=metadata)

customers_table = Table(
    "customers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("email", String(200), nullable=True),
)

_mapped = False


def map_entities() -> None:
    """Idempotently map the domain entities to their tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively(Customer, customers_table)
        _mapped = True
