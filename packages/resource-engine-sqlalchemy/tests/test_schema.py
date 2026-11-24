"""engine_tables declares the engine's tables on the application's MetaData,
with names the naming convention keeps stable."""

from __future__ import annotations

import pytest
from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables
from sqlalchemy import MetaData, PrimaryKeyConstraint


def test_only_what_is_asked_for():
    metadata = MetaData()
    tables = engine_tables(metadata, outbox=True, locks=True)
    assert set(metadata.tables) == {"resource_engine_outbox", "resource_engine_locks"}
    assert tables.inbox is None and tables.authz is None
    assert [t.name for t in tables.all()] == ["resource_engine_outbox", "resource_engine_locks"]


def test_every_table_with_custom_names():
    metadata = MetaData()
    tables = engine_tables(
        metadata,
        outbox=True,
        inbox=True,
        audit=True,
        seals=True,
        authz=True,
        documents=True,
        locks=True,
        names={"outbox": "billing_outbox", "authz": "billing_authz_"},
    )
    assert len(tables.all()) == 9
    assert "billing_outbox" in metadata.tables
    assert "billing_authz_policies" in metadata.tables


def test_seals_need_the_audit_table():
    with pytest.raises(ValueError, match="seals=True needs audit=True"):
        engine_tables(MetaData(), seals=True)


def test_naming_convention_names_every_constraint():
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    tables = engine_tables(
        metadata, outbox=True, inbox=True, audit=True, seals=True, authz=True, documents=True,
        locks=True,
    )  # fmt: skip
    for table in tables.all():
        pk = next(c for c in table.constraints if isinstance(c, PrimaryKeyConstraint))
        assert pk.name == f"pk_{table.name}"
        for index in table.indexes:
            assert index.name and index.name.startswith("ix_")
            assert len(index.name) <= 63  # PostgreSQL identifier limit
