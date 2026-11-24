"""PostgreSQL for the migration and contract tests (compose.emulators.yaml,
port 5439). Each test gets a database of its own, dropped afterwards."""

from __future__ import annotations

import os
import uuid

import pytest

ADMIN_URL = os.getenv(
    "POSTGRES_URL", "postgresql://resource_engine:resource_engine@127.0.0.1:5439/resource_engine"
)


def _problem() -> str | None:
    try:
        import psycopg

        with psycopg.connect(ADMIN_URL, connect_timeout=2):
            return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}".splitlines()[0]


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("postgres",)
    ]
    if not selected or (problem := _problem()) is None:
        return
    skip = pytest.mark.skip(reason=f"PostgreSQL not reachable at 127.0.0.1:5439 ({problem})")
    for item in selected:
        item.add_marker(skip)


@pytest.fixture
def postgres_database():
    """A fresh, empty database: yields its async SQLAlchemy URL."""
    import psycopg

    name = f"re_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = ADMIN_URL.rpartition("/")
    yield f"{base.replace('postgresql://', 'postgresql+psycopg://', 1)}/{name}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
