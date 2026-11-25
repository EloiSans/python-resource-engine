"""SQLAlchemy adapter over ``AsyncSession``: repositories, unit of work, outbox, inbox,
documents, permissions, the audit trail and job leases.

Domain entities stay plain classes; map them with SQLAlchemy's imperative mapping
(``registry.map_imperatively(Invoice, invoices_table)``) inside the module's
infrastructure layer, so the domain never imports SQLAlchemy.
"""

from resource_engine_sqlalchemy.audit import audit_seal_table, audit_table, map_audit
from resource_engine_sqlalchemy.authz import AuthzTables, authz_tables, map_authz
from resource_engine_sqlalchemy.documents import documents_table, map_documents
from resource_engine_sqlalchemy.idempotency import SqlAlchemyIdempotency, idempotency_table
from resource_engine_sqlalchemy.inbox import SqlAlchemyInbox, inbox_table
from resource_engine_sqlalchemy.locks import SqlAlchemyLocks, locks_table
from resource_engine_sqlalchemy.outbox import SqlAlchemyOutbox, outbox_table
from resource_engine_sqlalchemy.repository import SqlAlchemyRepository
from resource_engine_sqlalchemy.schema import (
    NAMING_CONVENTION,
    EngineTables,
    engine_tables,
    map_engine,
)
from resource_engine_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork, unit_of_work_factory

__all__ = [
    "NAMING_CONVENTION",
    "EngineTables",
    "AuthzTables",
    "SqlAlchemyIdempotency",
    "SqlAlchemyInbox",
    "SqlAlchemyLocks",
    "SqlAlchemyOutbox",
    "SqlAlchemyRepository",
    "SqlAlchemyUnitOfWork",
    "audit_seal_table",
    "audit_table",
    "authz_tables",
    "documents_table",
    "engine_tables",
    "idempotency_table",
    "inbox_table",
    "locks_table",
    "map_audit",
    "map_authz",
    "map_documents",
    "map_engine",
    "outbox_table",
    "unit_of_work_factory",
]
