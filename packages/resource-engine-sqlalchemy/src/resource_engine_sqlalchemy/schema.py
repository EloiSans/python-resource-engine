"""The engine's tables on your ``MetaData``, ready for Alembic autogenerate.

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import registry as orm_registry
from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables, map_engine

metadata = MetaData(naming_convention=NAMING_CONVENTION)
mapper_registry = orm_registry(metadata=metadata)
engine = engine_tables(metadata, outbox=True, audit=True, seals=True, authz=True)
map_engine(mapper_registry, engine)
uow = unit_of_work_factory(sessions, registry, outbox_table=engine.outbox)
```

Your own tables and the engine's live on the same ``MetaData``, so
``alembic revision --autogenerate`` writes one migration for both. See
docs/migrations.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import MetaData, Table
from sqlalchemy.orm import registry as orm_registry

from resource_engine_sqlalchemy.audit import audit_seal_table, audit_table, map_audit
from resource_engine_sqlalchemy.authz import AuthzTables, authz_tables, map_authz
from resource_engine_sqlalchemy.documents import documents_table, map_documents
from resource_engine_sqlalchemy.idempotency import idempotency_table
from resource_engine_sqlalchemy.inbox import inbox_table
from resource_engine_sqlalchemy.locks import locks_table
from resource_engine_sqlalchemy.outbox import outbox_table

#: Constraint and index names that are stable across databases, so later
#: migrations can drop or rename them. Pass it to your ``MetaData``.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


@dataclass(frozen=True)
class EngineTables:
    """The engine tables declared by ``engine_tables`` (``None`` when not asked for)."""

    outbox: Table | None = None
    inbox: Table | None = None
    audit: Table | None = None
    seals: Table | None = None
    authz: AuthzTables | None = None
    documents: Table | None = None
    locks: Table | None = None
    idempotency: Table | None = None

    def all(self) -> list[Table]:
        tables = [
            self.outbox,
            self.inbox,
            self.audit,
            self.seals,
            self.documents,
            self.locks,
            self.idempotency,
        ]
        if self.authz is not None:
            tables += [self.authz.policies, self.authz.roles, self.authz.assignments]
        return [t for t in tables if t is not None]


def engine_tables(
    metadata: MetaData,
    *,
    outbox: bool = False,
    inbox: bool = False,
    audit: bool = False,
    seals: bool = False,
    authz: bool = False,
    documents: bool = False,
    locks: bool = False,
    idempotency: bool = False,
    names: dict[str, Any] | None = None,
) -> EngineTables:
    """Declare the engine tables an application uses on ``metadata``.

    Args:
        outbox, inbox: Messaging (``OutboxDelivery``, ``MessageProcessor``).
        audit, seals: The audit trail and its seals (``AuditSealer``); ``seals``
            needs ``audit``.
        authz: Policies, roles and role assignments (``registry.register_authz``).
        documents: Attached documents (``registry.register_documents``).
        locks: Leases for ``JobRunner`` jobs with ``single=True``.
        idempotency: Results of writes sent with an idempotency key.
        names: Custom table names, by the same keys (``authz`` takes a prefix).
    """
    if seals and not audit:
        raise ValueError("seals=True needs audit=True: seals cover audit entries")
    names = names or {}

    def named(key: str, build: Any) -> Any:
        return build(metadata, names[key]) if key in names else build(metadata)

    return EngineTables(
        outbox=named("outbox", outbox_table) if outbox else None,
        inbox=named("inbox", inbox_table) if inbox else None,
        audit=named("audit", audit_table) if audit else None,
        seals=named("seals", audit_seal_table) if seals else None,
        authz=named("authz", authz_tables) if authz else None,
        documents=named("documents", documents_table) if documents else None,
        locks=named("locks", locks_table) if locks else None,
        idempotency=named("idempotency", idempotency_table) if idempotency else None,
    )


def map_engine(registry: orm_registry, tables: EngineTables) -> None:
    """Map the engine's entities onto the tables that need it (audit, seals,
    authz, documents). Safe to call more than once."""
    if tables.audit is not None:
        map_audit(registry, tables.audit, tables.seals)
    if tables.authz is not None:
        map_authz(registry, tables.authz)
    if tables.documents is not None:
        map_documents(registry, tables.documents)
