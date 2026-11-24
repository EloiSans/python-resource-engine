"""SQLAlchemy mapping of the tickets module. Only infrastructure imports SQLAlchemy."""

from __future__ import annotations

from helpdesk.tickets.domain.entities import Ticket
from sqlalchemy import Column, Integer, MetaData, String, Table, Text
from sqlalchemy.orm import registry as orm_registry

metadata = MetaData()
mapper_registry = orm_registry(metadata=metadata)

tickets_table = Table(
    "tickets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, nullable=False),
    Column("subject", String(200), nullable=False),
    Column("description", Text, nullable=True),
    Column("status", String(32), nullable=False),
)

_mapped = False


def map_entities() -> None:
    """Idempotently map the domain entities to their tables."""
    global _mapped
    if not _mapped:
        mapper_registry.map_imperatively(Ticket, tickets_table)
        _mapped = True
