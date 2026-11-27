"""The example's worker: ``uv run resource-engine worker`` (``[tool.resource-engine]
worker``).

- ``processes``: feeds the outbox to the process managers (the invoice payment
  process) and to the mailer (requested emails), exactly once through the inbox.
- ``timers``: due timed transitions (pending invoices after 30 days, overdue
  payments after 15).

Run it next to the app, on the same database (``DATABASE_URL``).
"""

from __future__ import annotations

import os
from datetime import timedelta

from resource_engine import TimerJob
from resource_engine.jobs import JobRunner
from resource_engine.messaging import MessageProcessor
from resource_engine.processes import ProcessFeed, ProcessRunner
from resource_engine_sqlalchemy import SqlAlchemyLocks
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from invoices.app import _blob_store, build_manager
from invoices.infrastructure.persistence import engine as engine_tables
from invoices.infrastructure.persistence import map_entities
from invoices.notifications import build_mailer


def build(database_url: str | None = None) -> JobRunner:
    map_entities()
    engine = create_async_engine(
        database_url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///invoices.db")
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    manager = build_manager(sessions, blob_store=_blob_store()[0])
    router = ProcessRunner(manager).router().include(build_mailer().router())
    processor = MessageProcessor(router, name="invoice-processes", manager=manager)
    runner = JobRunner(locks=SqlAlchemyLocks(sessions, engine_tables.locks))
    runner.add("processes", ProcessFeed(manager.uow_factory, processor), every=1, single=True)
    runner.add("timers", TimerJob(manager), every=timedelta(minutes=1), single=True)
    return runner
