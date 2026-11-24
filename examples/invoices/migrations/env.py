"""Alembic environment: the application's tables and the engine's, on one MetaData."""

from alembic import context
from invoices.infrastructure.persistence import metadata
from resource_engine_sqlalchemy.migrations import run_migrations

run_migrations(context, metadata)
