"""SQLAlchemy adapter over ``AsyncSession``: repositories, unit of work, outbox, inbox, documents.

Domain entities stay plain classes; map them with SQLAlchemy's imperative mapping
(``registry.map_imperatively(Invoice, invoices_table)``) inside the module's
infrastructure layer, so the domain never imports SQLAlchemy.
"""

from resource_engine_sqlalchemy.authz import AuthzTables, authz_tables, map_authz
from resource_engine_sqlalchemy.documents import documents_table, map_documents
from resource_engine_sqlalchemy.inbox import SqlAlchemyInbox, inbox_table
from resource_engine_sqlalchemy.outbox import SqlAlchemyOutbox, outbox_table
from resource_engine_sqlalchemy.repository import SqlAlchemyRepository
from resource_engine_sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork, unit_of_work_factory

__all__ = [
    "AuthzTables",
    "SqlAlchemyInbox",
    "SqlAlchemyOutbox",
    "SqlAlchemyRepository",
    "SqlAlchemyUnitOfWork",
    "authz_tables",
    "documents_table",
    "inbox_table",
    "map_authz",
    "map_documents",
    "outbox_table",
    "unit_of_work_factory",
]
