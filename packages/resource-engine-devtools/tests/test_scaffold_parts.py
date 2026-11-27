"""``resource-engine scaffold hook|event|slot|reference|policy``: each part is
written and declared on the resource, and the module stays importable,
registrable, formatted and passing ``check`` and its own tests."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from resource_engine import ResourceRegistry
from resource_engine_devtools import source_edit as edit
from resource_engine_devtools.cli import main
from resource_engine_devtools.events_check import check_events
from resource_engine_devtools.gates_check import check_gates, load_policies
from resource_engine_devtools.hooks_check import check_hooks

REPO = Path(__file__).resolve().parents[3]
PKG = ["--package", "scafparts.orders"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.split(".")[0] == "scafparts"]:
        del sys.modules[name]


def run(project, *args) -> int:
    return main(["scaffold", *args, *PKG, "--root", str(project)])


def _ruff_clean(project) -> None:
    ruff = [sys.executable, "-m", "ruff"]
    config = ["--config", str(REPO / "pyproject.toml")]
    check = subprocess.run([*ruff, "check", *config, str(project)], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout
    diff = subprocess.run(
        [*ruff, "format", "--diff", *config, str(project)], capture_output=True, text=True
    )
    assert diff.returncode == 0, diff.stdout + diff.stderr


def test_every_part_is_declared_and_the_module_stays_clean(project, capsys):
    (project / "scafparts").mkdir()
    (project / "scafparts/__init__.py").write_text("")
    assert main([
        "scaffold", "resource", "customers", "--package", "scafparts.customers",
        "--root", str(project), "--field", "name:str",
    ]) == 0  # fmt: skip
    assert main([
        "scaffold", "resource", "orders", *PKG, "--root", str(project),
        "--field", "total:Decimal", "--field", "notes:text?",
        "--states", "draft,placed,shipped",
        "--transitions", "place:draft>placed,ship:placed>shipped",
    ]) == 0  # fmt: skip
    assert run(project, "hook", "orders", "check_total", "--on", "before_create",
               "--on", "before_transition:place") == 0  # fmt: skip
    assert run(project, "hook", "orders", "tell_warehouse", "--on", "after_commit",
               "--effect") == 0  # fmt: skip
    assert run(project, "hook", "orders", "keep_notes", "--on", "after_update") == 0
    assert run(project, "event", "orders", "OrderShipped", "--field", "carrier:str",
               "--field", "shipped_on:date") == 0  # fmt: skip
    assert run(project, "slot", "orders", "delivery_note", "--content-type", "application/pdf",
               "--max-mb", "5", "--required-for", "ship") == 0  # fmt: skip
    assert run(project, "slot", "orders", "photos", "--multiple") == 0
    assert run(project, "reference", "orders", "customer_id", "--to", "customers",
               "--relation", "customer") == 0  # fmt: skip
    assert run(project, "action", "orders", "Recalculate") == 0
    assert run(project, "policy", "orders", "--role", "clerk") == 0
    assert run(project, "policy", "orders", "--role", "auditor", "--read-only") == 0
    capsys.readouterr()

    _ruff_clean(project)
    resource = importlib.import_module("scafparts.orders.application.resource").OrderResource
    customers = importlib.import_module("scafparts.customers.application.resource").CustomerResource
    registry = ResourceRegistry()
    registry.register(customers)
    definition = registry.register(resource).definition()
    assert {e.__name__ for e in definition.events} == {"OrderShipped"}
    assert set(definition.document_slots) == {"delivery_note", "photos"}
    assert definition.document_slots["delivery_note"].required_for == ("ship",)
    assert set(definition.references) == {"customer"}
    assert set(definition.actions) == {"recalculate"}
    hooks = resource.hooks
    assert [h.__name__ for h in hooks["before_create"]] == ["validate", "check_total"]
    assert [h.__name__ for h in hooks["after_update"]] == ["validate", "keep_notes"]
    assert [h.__name__ for h in hooks["before_transition:place"]] == ["check_total"]
    assert "customer_id" in definition.schemas["create"].model_fields
    assert check_hooks(registry) == [] and check_events(registry) == []

    policies = load_policies("scafparts.orders.authz:SEED_POLICIES")
    assert [p["name"] for p in policies] == ["Clerk manages orders", "Auditor reads orders"]
    clerk, auditor = (set(p["actions"]) for p in policies)
    assert {"orders:create", "orders:transition:ship", "orders:action:recalculate",
            "orders:attach:photos"} <= clerk  # fmt: skip
    assert auditor == {"orders:list", "orders:get", "orders:download:delivery_note",
                       "orders:download:photos"}  # fmt: skip
    gates = check_gates(registry, None, policies)
    assert not [v for v in gates if "orders:" in v.message]  # every operation is granted

    # what the next steps ask for: the referenced resource and a blob store in the tests
    test_file = project / "scafparts/orders/tests/test_orders.py"
    text = test_file.read_text()
    text = text.replace(
        "    registry.register(OrderResource)\n",
        "    registry.register(CustomerResource)\n    registry.register(OrderResource)\n"
        "    registry.register_documents()\n",
    ).replace(
        "store.unit_of_work, event_publisher=publisher)",
        "store.unit_of_work, event_publisher=publisher, blob_store=InMemoryBlobStore())",
    )
    text = text.replace(
        "from resource_engine.testing import InMemoryStore",
        "from resource_engine.testing import InMemoryBlobStore, InMemoryStore",
    ).replace(
        "from scafparts.orders.application.resource import OrderResource",
        "from scafparts.customers.application.resource import CustomerResource\n"
        "from scafparts.orders.application.resource import OrderResource",
    )
    test_file.write_text(text)
    env = os.environ | {"PYTHONPATH": str(project)}
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(project / "scafparts/orders/tests")],
        cwd=project, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert tests.returncode == 0, tests.stdout + tests.stderr


def test_names_already_there_are_refused(project, capsys):
    assert main(["scaffold", "resource", "orders", *PKG, "--root", str(project)]) == 0
    assert run(project, "hook", "orders", "check_total", "--on", "before_create") == 0
    assert run(project, "hook", "orders", "check_total", "--on", "after_update") == 2
    assert run(project, "event", "orders", "OrderPlaced") == 0
    assert run(project, "event", "orders", "OrderPlaced") == 2
    assert run(project, "slot", "orders", "scan") == 0
    assert run(project, "slot", "orders", "scan") == 2
    assert run(project, "reference", "orders", "customer_id", "--to", "customers") == 0
    assert run(project, "reference", "orders", "customer_id", "--to", "customers") == 2
    assert run(project, "policy", "orders", "--role", "clerk") == 0
    assert run(project, "policy", "orders", "--role", "clerk") == 2
    out = capsys.readouterr().out
    assert "already defines check_total" in out and "already has a 'scan' slot" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["hook", "orders", "f", "--on", "before_create", "--effect"], "after_commit"),
        (["hook", "orders", "f", "--on", "before_lunch"], "Unknown hook point"),
        (["hook", "orders", "f"], "--on"),
        (["hook", "nothing", "f", "--on", "after_update"], "No Resource"),
        (["reference", "orders", "x_id", "--to", "c", "--on-delete", "set_null", "--required"],
         "optional"),
        (["event", "orders", "lowercase"], "PascalCase"),
    ],
)  # fmt: skip
def test_bad_arguments(project, capsys, args, message):
    assert main(["scaffold", "resource", "orders", *PKG, "--root", str(project)]) == 0
    capsys.readouterr()
    assert run(project, *args) == 2
    assert message in capsys.readouterr().out


def test_dry_run_changes_nothing(project, capsys):
    assert main(["scaffold", "resource", "orders", *PKG, "--root", str(project)]) == 0
    resource = project / "scafparts/orders/application/resource.py"
    before = resource.read_text()
    assert run(project, "slot", "orders", "scan", "--dry-run") == 0
    assert run(project, "hook", "orders", "f", "--on", "after_update", "--dry-run") == 0
    assert resource.read_text() == before
    assert not (project / "scafparts/orders/domain/events.py").exists()


def test_edits_keep_a_hand_written_resource_intact(tmp_path):
    """The reference module's resource: comments and layout survive."""
    source = (REPO / "examples/invoices/application/resource.py").read_text()
    edited = edit.add_to_dict_list(source, "InvoiceResource", "hooks", "before_update", "hooks.x")
    edited = edit.add_to_dict_list(edited, "InvoiceResource", "hooks", "after_create", "hooks.y")
    edited = edit.add_to_dict(
        edited, "InvoiceResource", "references", "tag_id", 'Reference("tags")'
    )
    edited = edit.append_to_list(edited, "InvoiceResource", "actions", "Archive")
    edited = edit.append_to_list(edited, "InvoiceResource", "documents", 'DocumentSlot("scan")')
    edited = edit.add_import(edited, "invoices.application.actions", "Archive")
    assert edited.count("#") == source.count("#")  # every comment kept
    assert '"before_update": [hooks.ensure_editable, hooks.x]' in edited
    assert '"after_create": [hooks.y]' in edited
    assert (
        "actions = [\n        RecalculateTotals,\n        RenderSummary,\n        Archive,\n    ]"
        in edited
    )
    assert "import Archive, RecalculateTotals, RenderSummary" in edited
    compile(edited, "resource.py", "exec")
    path = tmp_path / "resource.py"
    path.write_text(edited)
    config = ["--config", str(REPO / "pyproject.toml")]
    diff = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--diff", *config, str(path)],
        capture_output=True, text=True,
    )  # fmt: skip
    assert diff.returncode == 0, diff.stdout


def test_imports_land_in_their_section():
    source = "from __future__ import annotations\n\nfrom pydantic import BaseModel\n"
    assert edit.add_import(source, "decimal", "Decimal") == (
        "from __future__ import annotations\n\nfrom decimal import Decimal\n\n"
        "from pydantic import BaseModel\n"
    )
    assert edit.add_import(source, "pydantic", "Field") == (
        "from __future__ import annotations\n\nfrom pydantic import BaseModel, Field\n"
    )
