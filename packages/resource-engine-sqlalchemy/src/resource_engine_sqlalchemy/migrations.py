"""Alembic integration (``pip install resource-engine-sqlalchemy[alembic]``).

``resource-engine db init`` writes an ``env.py`` that is three lines::

    from alembic import context
    from resource_engine_sqlalchemy.migrations import run_migrations
    from billing.infrastructure.persistence import metadata

    run_migrations(context, metadata)

``run_migrations`` runs offline (``alembic upgrade --sql``) or online with an
async engine, from ``DATABASE_URL`` or ``sqlalchemy.url`` in ``alembic.ini``.
SQLite gets ``render_as_batch`` (it cannot ``ALTER`` most things in place) and
every database ``compare_type``, so autogenerate notices type changes.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

#: Environment variable read before ``sqlalchemy.url`` in ``alembic.ini``.
URL_VARIABLE = "DATABASE_URL"


def database_url(context: Any) -> str:
    url = os.getenv(URL_VARIABLE) or context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(f"No database URL: set {URL_VARIABLE} or sqlalchemy.url in alembic.ini")
    return url


def context_options(dialect_name: str) -> dict[str, Any]:
    """Options for ``context.configure`` that suit the engine's tables."""
    return {"render_as_batch": dialect_name == "sqlite", "compare_type": True}


def run_migrations(context: Any, metadata: MetaData, *, url: str | None = None) -> None:
    """Run Alembic for ``metadata`` (call it from ``env.py``)."""
    url = url or database_url(context)
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            **context_options(make_url(url).get_backend_name()),
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    asyncio.run(_run_online(context, metadata, url))


async def _run_online(context: Any, metadata: MetaData, url: str) -> None:
    section = dict(context.config.get_section(context.config.config_ini_section, {}))
    section["sqlalchemy.url"] = url
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync, context, metadata)
    finally:
        await engine.dispose()


def _run_sync(connection: Connection, context: Any, metadata: MetaData) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        **context_options(connection.dialect.name),
    )
    with context.begin_transaction():
        context.run_migrations()
