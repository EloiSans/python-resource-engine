"""The reference module shipped inside the skill (``skill/examples/invoices``).

It is a copy of the repository's ``examples/invoices``, which runs in the
framework's tests. ``test_skill_example_is_in_sync`` fails when they drift;
refresh the copy with ``python -m resource_engine_devtools.ai.example``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: Files of ``examples/invoices`` copied into the skill.
EXAMPLE_FILES = (
    "__init__.py",
    "bootstrap.py",
    "authz.py",
    "app.py",
    "domain/__init__.py",
    "domain/entities.py",
    "domain/events.py",
    "domain/state_machine.py",
    "application/__init__.py",
    "application/schemas.py",
    "application/customers.py",
    "application/balances.py",
    "application/tags.py",
    "application/processes.py",
    "worker.py",
    "notifications.py",
    "templates/email/base.html",
    "templates/email/invoice_approved.subject.txt",
    "templates/email/invoice_approved.txt",
    "templates/email/invoice_approved.html",
    "templates/ca/email/invoice_approved.subject.txt",
    "templates/ca/email/invoice_approved.txt",
    "templates/ca/email/invoice_approved.html",
    "application/resource.py",
    "application/hooks.py",
    "application/actions.py",
    "infrastructure/__init__.py",
    "infrastructure/persistence.py",
    "tests/conftest.py",
    "tests/test_invoices_engine.py",
)

SKILL_DIR = Path(__file__).parent / "skill"
EXAMPLE_DIR = SKILL_DIR / "examples" / "invoices"


def sync(source: Path, target: Path = EXAMPLE_DIR) -> list[str]:
    """Copy the example files from ``source``; returns the files that changed."""
    changed = []
    for name in EXAMPLE_FILES:
        src, dst = source / name, target / name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(name)
    return changed


if __name__ == "__main__":  # pragma: no cover
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    for name in sync(repo / "examples" / "invoices"):
        print(f"updated {name}")
