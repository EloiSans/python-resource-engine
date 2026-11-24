"""``resource-engine db init`` writes a working Alembic setup."""

from __future__ import annotations

import sys
import textwrap

import pytest
from resource_engine_devtools.cli import main

MODULE = textwrap.dedent(
    """
    from resource_engine_sqlalchemy import NAMING_CONVENTION, engine_tables
    from sqlalchemy import Column, Integer, MetaData, String, Table

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    Table("notes", metadata, Column("id", Integer, primary_key=True), Column("text", String(50)))
    engine = engine_tables(metadata, outbox=True)
    """
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dbapp.py").write_text(MODULE)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.resource-engine]\nmetadata = "dbapp:metadata"\npaths = ["src"]\n'
    )
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    sys.modules.pop("dbapp", None)


def test_init_then_autogenerate_and_upgrade(project, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    assert main(["db", "init"]) == 0
    assert (
        (project / "migrations/env.py").read_text().endswith("run_migrations(context, metadata)\n")
    )
    assert "prepend_sys_path = %(here)s/src" in (project / "alembic.ini").read_text()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{project / 'app.db'}")
    config = Config(str(project / "alembic.ini"))
    command.revision(config, message="initial", autogenerate=True)
    (revision,) = (project / "migrations/versions").glob("*_initial.py")
    assert "resource_engine_outbox" in revision.read_text()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{project / 'app.db'}")
    assert {"notes", "resource_engine_outbox"} <= set(inspect(engine).get_table_names())
    engine.dispose()
    command.check(config)


def test_init_refuses_to_overwrite(project, capsys):
    assert main(["db", "init", "--metadata", "dbapp:metadata"]) == 0
    with pytest.raises(SystemExit, match="already exist"):
        main(["db", "init", "--metadata", "dbapp:metadata"])
    assert main(["db", "init", "--metadata", "dbapp:metadata", "--force"]) == 0


def test_init_needs_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="No metadata given"):
        main(["db", "init"])
    with pytest.raises(SystemExit, match="module:attribute"):
        main(["db", "init", "--metadata", "nonsense"])
