# Migrations

The engine's tables (outbox, inbox, audit trail and seals, permissions,
documents, leases) are declared on **your** `MetaData`, next to your own.
Alembic's autogenerate then writes one migration for both, with the names you
chose for your tables.

## Setup

```python
# billing/infrastructure/persistence.py
from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables, map_engine
from sqlalchemy import MetaData
from sqlalchemy.orm import registry as orm_registry

metadata = MetaData(naming_convention=NAMING_CONVENTION)
mapper_registry = orm_registry(metadata=metadata)
# ... your tables ...
engine = engine_tables(metadata, outbox=True, audit=True, seals=True, authz=True, documents=True)
map_engine(mapper_registry, engine)
```

| Option | Tables | Used by |
|---|---|---|
| `outbox` | `resource_engine_outbox` | `OutboxDelivery`, `OutboxRelay` |
| `inbox` | `resource_engine_inbox` | `MessageProcessor` |
| `audit`, `seals` | `resource_engine_audit_log`, `resource_engine_audit_seals` | `audit=True`, `AuditSealer` |
| `authz` | `resource_engine_authz_policies`, `_roles`, `_role_assignments` | `registry.register_authz()` |
| `documents` | `resource_engine_documents` | `registry.register_documents()` |
| `locks` | `resource_engine_locks` | `JobRunner` jobs with `single=True` |

- **Custom names:** pass `names={"outbox": "billing_outbox", "authz": "billing_authz_"}`.
- **Wiring:** pass `engine.outbox` and `engine.inbox` to `unit_of_work_factory`.
- **The naming convention** gives every primary key, index and constraint a
  stable name, such as `pk_resource_engine_outbox`. A later migration can only
  drop or rename a constraint that has a name.

Then set up Alembic and create the first migration:

```bash
pip install "resource-engine-sqlalchemy[alembic]"
resource-engine db init --metadata billing.infrastructure.persistence:metadata
alembic revision --autogenerate -m "initial schema"
DATABASE_URL=postgresql+psycopg://user:pass@host/db alembic upgrade head
```

`db init` writes `alembic.ini` and `migrations/`.

- **`env.py`:** it calls `resource_engine_sqlalchemy.migrations.run_migrations`.
  That runs async drivers and offline SQL (`alembic upgrade head --sql`). It
  turns on `render_as_batch` for SQLite, which can't `ALTER` most things in
  place, and `compare_type`, so type changes are detected.
- **Defaults:** the metadata comes from `[tool.resource-engine] metadata` and
  the import paths from `paths`.
- **The URL** comes from `DATABASE_URL`, or from `sqlalchemy.url` in
  `alembic.ini`.

## In CI

```bash
alembic upgrade head
alembic check        # fails when the code has changes without a migration
```

The reference module does exactly this in
`packages/resource-engine-sqlalchemy/tests/test_migrations.py`. Its migrations
are in `examples/invoices/migrations`, and they are tested on SQLite and
PostgreSQL: upgrade, `check`, then downgrade.

## When the engine's schema changes

A new version of the engine may change its tables. The change is listed
below. Run `alembic revision --autogenerate` after upgrading and review the
migration, just as you would for one of your own changes. New columns on
existing engine tables are nullable or have a server default, so the
migration works on tables that already have rows.

| Version | Table | Change |
|---|---|---|
| 0.1.0 | all | Initial tables |

Before 0.1.0, unreleased versions added `resource_engine_audit_log.seal` (an
indexed integer, nullable), the `resource_engine_audit_seals` table and
`resource_engine_locks`.
