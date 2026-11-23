from __future__ import annotations

import json
from dataclasses import dataclass

from invoices.bootstrap import registry
from pydantic import BaseModel
from resource_engine import Resource, ResourceRegistry
from resource_engine_devtools.check import check_domain_imports
from resource_engine_devtools.cli import main
from resource_engine_devtools.documentation import introspect, validate
from resource_engine_devtools.documentation.generators import render_markdown, render_state_machine


def test_mermaid_comes_from_executable_machine():
    sm = introspect(registry).resources[0].state_machine
    diagram = render_state_machine(sm)
    assert diagram.startswith("stateDiagram-v2")
    assert "[*] --> draft" in diagram
    assert "pending --> rejected: reject" in diagram
    assert "paid --> [*]" in diagram


def test_markdown_uses_docstrings_and_definitions():
    pages = render_markdown(introspect(registry))
    page = pages["resources/invoices.md"]
    assert "Gestió de factures de l'organització." in page
    assert "## Actors" in page
    assert "`POST /invoices/{id}/transitions/reject` | RejectInvoice" in page
    assert "```mermaid" in page
    assert "[invoices](resources/invoices.md)" in pages["index.md"]


@dataclass
class Note:
    text: str
    id: int | None = None


class NoteIn(BaseModel):
    text: str


class NoteOut(BaseModel):
    id: int
    text: str


class NoteResource(Resource):
    model = Note
    schemas = {"create": NoteIn, "read": NoteOut}
    operations = ["create", "get"]
    operation_docs = {"get": "Read a note."}


def test_validator_flags_undocumented_resource_and_operation():
    reg = ResourceRegistry()
    reg.register(NoteResource)
    errors = {(f.code, f.target) for f in validate(introspect(reg)) if f.level == "error"}
    assert ("RE-D001", f"{__name__}.NoteResource") in errors
    assert ("RE-D002", "notes.create") in errors
    assert ("RE-D002", "notes.get") not in errors


def test_check_detects_infrastructure_import_in_domain(tmp_path):
    domain = tmp_path / "billing" / "domain"
    domain.mkdir(parents=True)
    (domain / "entities.py").write_text("import os\nfrom sqlalchemy import Column\n")
    (tmp_path / "billing" / "infrastructure.py").write_text("import sqlalchemy\n")
    violations = check_domain_imports(tmp_path)
    assert [(v.code, v.line) for v in violations] == [("RE-C001", 2)]


def test_cli_json_outputs(capsys, tmp_path):
    assert main(["inspect", "invoices", "--json", "--app", "invoices.bootstrap:registry"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["state_machine"]["initial"] == "draft"
    assert data[0]["hooks"]["after_commit"] == ["invoices.application.hooks.notify_approval"]

    assert main(["docs", "validate", "--json", "--app", "invoices.bootstrap:registry"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_ai_context_check_detects_drift(capsys, tmp_path):
    out = tmp_path / "context.md"
    args = ["ai", "context", "--app", "invoices.bootstrap:registry", "--out", str(out)]
    assert main([*args, "--check"]) == 1
    assert main(args) == 0
    assert main([*args, "--check"]) == 0
    out.write_text(out.read_text() + "stale")
    assert main([*args, "--check"]) == 1
