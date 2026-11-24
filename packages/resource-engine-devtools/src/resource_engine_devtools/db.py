"""``resource-engine db init``: an Alembic setup for an application that keeps
its tables and the engine's on one ``MetaData`` (see docs/migrations.md)."""

from __future__ import annotations

import re
from pathlib import Path

ALEMBIC_INI = """\
# Alembic, set up by resource-engine db init. The database URL comes from
# DATABASE_URL, or from sqlalchemy.url below.
[alembic]
script_location = %(here)s/{directory}
prepend_sys_path = {sys_path}
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s
path_separator = space
# sqlalchemy.url = sqlite+aiosqlite:///app.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
"""

ENV_PY = '''\
"""Alembic environment: the application's tables and the engine's, on one MetaData."""

from alembic import context
from resource_engine_sqlalchemy.migrations import run_migrations

from {module} import {attribute}

run_migrations(context, {attribute})
'''

SCRIPT_MAKO = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def init(
    root: Path,
    metadata: str,
    *,
    directory: str = "migrations",
    sys_path: list[str] | None = None,
    force: bool = False,
) -> list[Path]:
    """Write ``alembic.ini`` and ``<directory>/{env.py,script.py.mako,versions/}``.

    Args:
        root: Where ``alembic.ini`` goes (usually the project root).
        metadata: ``module:attribute`` of the application's ``MetaData``.
        directory: The migrations folder, relative to ``root``.
        sys_path: Folders (relative to ``root``) put on ``sys.path`` so the
            metadata imports, e.g. ``["src"]``.
    """
    module, _, attribute = metadata.partition(":")
    if not re.fullmatch(r"[A-Za-z_][\w.]*", module) or not attribute.isidentifier():
        raise SystemExit(f"--metadata must be module:attribute, not {metadata!r}")
    files = {
        root / "alembic.ini": ALEMBIC_INI.format(
            directory=directory,
            sys_path=" ".join(
                f"%(here)s/{p}" if p != "." else "%(here)s" for p in (sys_path or ["."])
            ),
        ),
        root / directory / "env.py": ENV_PY.format(module=module, attribute=attribute),
        root / directory / "script.py.mako": SCRIPT_MAKO,
        root / directory / "versions" / ".gitkeep": "",
    }
    existing = [path for path in files if path.exists()]
    if existing and not force:
        raise SystemExit(
            f"{', '.join(str(p) for p in existing)} already exist; pass --force to replace them"
        )
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(files)
