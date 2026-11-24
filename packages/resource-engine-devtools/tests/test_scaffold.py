"""``resource-engine scaffold``: the generated module imports, registers, is
formatted, passes ``check`` and its own tests, and documents itself with TODOs
that ``docs validate`` reports (RE-D010)."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from resource_engine import ResourceRegistry
from resource_engine_devtools import scaffold
from resource_engine_devtools.check import check_domain_imports
from resource_engine_devtools.cli import main
from resource_engine_devtools.documentation import introspect, validate
from resource_engine_devtools.hooks_check import check_hooks

REPO = Path(__file__).resolve().parents[3]
TRANSITIONS = (
    "place:draft>placed,ship:placed>shipped,cancel:draft>cancelled,cancel:placed>cancelled"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.split(".")[0] == "scafshop"]:
        del sys.modules[name]


def generate(project, *extra):
    args = [
        "scaffold", "resource", "orders", "--package", "scafshop.orders", "--root", str(project),
        "--field", "customer:str", "--field", "total:Decimal", "--field", "due:date?",
        "--field", "notes:text?", "--versioned", *extra,
    ]  # fmt: skip
    assert main(args) == 0


def test_generated_module_is_complete_and_clean(project, capsys):
    generate(project, "--states", "draft,placed,shipped,cancelled", "--transitions", TRANSITIONS)
    assert main([
        "scaffold", "action", "orders", "ApplyDiscount", "--package", "scafshop.orders",
        "--root", str(project), "--input", "percent:Decimal",
    ]) == 0  # fmt: skip
    capsys.readouterr()
    (project / "scafshop" / "__init__.py").write_text("")
    base = project / "scafshop" / "orders"
    for name in ("domain/entities.py", "domain/state_machine.py", "application/schemas.py",
                 "application/resource.py", "application/hooks.py", "application/actions.py",
                 "infrastructure/persistence.py", "tests/test_orders.py"):  # fmt: skip
        assert (base / name).is_file(), name

    resource = importlib.import_module("scafshop.orders.application.resource").OrderResource
    resource.actions = [
        importlib.import_module("scafshop.orders.application.actions").ApplyDiscount
    ]
    registry = ResourceRegistry()
    definition = registry.register(resource).definition()
    assert definition.name == "orders" and definition.version_field == "version"
    assert set(definition.transitions) == {"place", "ship", "cancel"}
    assert "status" not in definition.schemas["create"].model_fields

    assert check_domain_imports(project) == [] and check_hooks(registry) == []
    findings = validate(introspect(registry, None))
    assert not [f for f in findings if f.level == "error"]
    assert {f.code for f in findings if f.level == "warning"} == {"RE-D010"}

    ruff = [sys.executable, "-m", "ruff"]
    config = ["--config", str(REPO / "pyproject.toml")]
    assert subprocess.run([*ruff, "check", *config, str(project)]).returncode == 0
    assert subprocess.run([*ruff, "format", "--check", *config, str(project)]).returncode == 0
    env = os.environ | {"PYTHONPATH": str(project)}
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(base / "tests")],
        cwd=project, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert "2 passed" in tests.stdout


def test_plain_fields_are_formatted(project, capsys):
    args = ["scaffold", "resource", "notes", "--package", "scafshop.notes", "--root", str(project)]
    assert main([*args, "--field", "title:str", "--field", "body:text?"]) == 0
    ruff = [sys.executable, "-m", "ruff"]
    config = ["--config", str(REPO / "pyproject.toml")]
    assert subprocess.run([*ruff, "check", *config, str(project)]).returncode == 0
    assert subprocess.run([*ruff, "format", "--check", *config, str(project)]).returncode == 0


def test_never_overwrites(project, capsys):
    generate(project)
    capsys.readouterr()
    assert main(["scaffold", "resource", "orders", "--package", "scafshop.orders",
                 "--root", str(project)]) == 2  # fmt: skip
    assert "already exists" in capsys.readouterr().out
    machine = ["scaffold", "state-machine", "Order", "--package", "scafshop.orders",
               "--root", str(project)]  # fmt: skip
    assert main(machine) == 0
    assert main(machine) == 2
    assert main([*machine, "--force"]) == 0


def test_dry_run_writes_nothing(project, capsys):
    assert main(["scaffold", "resource", "orders", "--package", "scafshop.orders",
                 "--root", str(project), "--dry-run", "--json"]) == 0  # fmt: skip
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] and len(result["written"]) == 10 and result["next_steps"]
    assert not (project / "scafshop").exists()


def test_action_needs_a_module_and_a_new_name(project, capsys):
    action = ["scaffold", "action", "orders", "Archive", "--package", "scafshop.orders",
              "--root", str(project)]  # fmt: skip
    assert main(action) == 2
    generate(project)
    assert main(action) == 0
    assert main(action) == 2
    assert "already defines Archive" in capsys.readouterr().out


def test_default_root_is_the_first_pyproject_path(project):
    (project / "pyproject.toml").write_text('[tool.resource-engine]\npaths = ["src"]\n')
    assert main(["scaffold", "resource", "orders", "--package", "scafshop.orders"]) == 0
    assert (project / "src/scafshop/orders/application/resource.py").is_file()


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda: scaffold.parse_fields(["Bad:str"]), "Invalid field"),
        (lambda: scaffold.parse_fields(["x:money"]), "Unknown type"),
        (lambda: scaffold.parse_fields(["status:str"]), "generated"),
        (lambda: scaffold.parse_fields(["a:str", "a:int"]), "Duplicate"),
        (lambda: scaffold.parse_machine("a,b", "go:a>z"), "unknown state"),
        (lambda: scaffold.parse_machine("a,b", "nope"), "Bad transition"),
        (lambda: scaffold.parse_machine("a", None), "at least two states"),
        (lambda: scaffold.package_dir(Path("."), "Bad.Name"), "Invalid package"),
    ],
)
def test_bad_arguments(call, match):
    with pytest.raises(scaffold.ScaffoldError, match=match):
        call()


def test_default_machine():
    states, transitions = scaffold.parse_machine(None, None)
    assert states == ["draft", "active", "closed"]
    assert [(t.event, t.source, t.target) for t in transitions] == [
        ("activate", "draft", "active"),
        ("close", "active", "closed"),
    ]


def test_reference_module_has_no_placeholders():
    from invoices.bootstrap import registry

    assert not [f for f in validate(introspect(registry, None)) if f.code == "RE-D010"]
