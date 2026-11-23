"""Tables and imperative mapping for the permission resources."""

from __future__ import annotations

from dataclasses import dataclass

from resource_engine.authz import Policy, Role, RoleAssignment
from sqlalchemy import JSON, Boolean, Column, DateTime, MetaData, String, Table, Text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import registry as orm_registry


@dataclass(frozen=True)
class AuthzTables:
    policies: Table
    roles: Table
    assignments: Table


def authz_tables(metadata: MetaData, prefix: str = "resource_engine_authz_") -> AuthzTables:
    """Declare the permission tables on your metadata (create them with migrations)."""
    return AuthzTables(
        policies=Table(
            f"{prefix}policies",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("name", String(200), nullable=False),
            Column("description", Text),
            Column("effect", String(5), nullable=False),
            Column("subjects", JSON, nullable=False),
            Column("actions", JSON, nullable=False),
            Column("condition", JSON),
            Column("enabled", Boolean, nullable=False, default=True),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by", String(200)),
            Column("updated_at", DateTime(timezone=True)),
            Column("updated_by", String(200)),
        ),
        roles=Table(
            f"{prefix}roles",
            metadata,
            Column("name", String(100), primary_key=True),
            Column("description", Text),
            Column("created_at", DateTime(timezone=True)),
        ),
        assignments=Table(
            f"{prefix}role_assignments",
            metadata,
            Column("id", String(32), primary_key=True),
            Column("role", String(100), nullable=False, index=True),
            Column("principal", String(300), index=True),
            Column("group", String(300), index=True),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by", String(200)),
        ),
    )


def map_authz(registry: orm_registry, tables: AuthzTables) -> None:
    """Map the permission entities (once per process; later calls are ignored)."""
    for entity, table in (
        (Policy, tables.policies),
        (Role, tables.roles),
        (RoleAssignment, tables.assignments),
    ):
        if sa_inspect(entity, raiseerr=False) is None:
            registry.map_imperatively(entity, table)
