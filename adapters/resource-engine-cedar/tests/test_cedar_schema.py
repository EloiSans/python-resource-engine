"""The Cedar schema generated from the registry: policy validation, and the
engine sending only what the schema declares."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from invoices.bootstrap import registry
from resource_engine.ports.authz import AuthzRequest
from resource_engine_cedar import CedarEngine, cedar_schema, validate
from resource_engine_devtools.cli import main

TYPES = {"invoices": "Invoice"}

GOOD = """
permit(principal in Role::"approver", action == Action::"invoices:transition:approve",
       resource is Invoice)
when { resource.amount.lessThan(decimal("10000.0")) && resource has status
       && resource.status == "pending" };
permit(principal in Role::"issuer", action == Action::"invoices:create", resource)
when { context has input && context.input.amount.lessThanOrEqual(decimal("1000.0")) };
permit(principal, action == Action::"invoices:get", resource is Invoice)
when { principal has tenant && resource has organization
       && resource.organization == principal.tenant };
"""


def test_the_schema_describes_resources_actions_and_inputs():
    schema = cedar_schema(registry, entity_types=TYPES)[""]
    invoice = schema["entityTypes"]["Invoice"]["shape"]["attributes"]
    assert invoice["amount"] == {"type": "Extension", "name": "decimal"}  # always there
    assert invoice["status"] == {"type": "String", "required": False}  # str | None
    assert schema["entityTypes"]["User"]["memberOfTypes"] == ["Role"]
    reject = schema["actions"]["invoices:transition:reject"]["appliesTo"]
    assert reject["resourceTypes"] == ["Invoice"]
    assert reject["context"]["attributes"]["input"]["attributes"]["reason"] == {"type": "String"}
    assert "invoices:read:notes" in schema["actions"]  # field actions too


def test_validation_catches_misspellings_and_types():
    schema = cedar_schema(registry, entity_types=TYPES)
    assert validate(GOOD, schema) == []
    bad = (
        'permit(principal, action == Action::"invoices:transition:aprove", resource);\n'
        'permit(principal, action == Action::"invoices:get", resource is Invoice)'
        " when { resource.ammount == 3 };\n"
        'permit(principal, action == Action::"invoices:get", resource is Invoice)'
        ' when { resource.amount == "3" };\n'
    )
    problems = " ".join(validate(bad, schema))
    assert "unrecognized action" in problems and "ammount" in problems
    assert "compatible" in problems or "type" in problems


async def test_the_engine_sends_only_what_the_schema_declares():
    engine = CedarEngine(
        GOOD, schema=cedar_schema(registry, entity_types=TYPES), entity_types=TYPES
    )
    subject = {
        "id": "oidc:a", "subject": "a", "provider": "oidc", "roles": ["approver"],
        "groups": [], "scopes": [], "tenant": "acme", "service": False,
        "claims": {"sub": "a", "locale": "ca"},  # not in the schema: left out
    }  # fmt: skip
    record = {
        "id": 7, "customer": "ACME", "amount": Decimal("120.50"), "status": "pending",
        "delivery_status": "unsent", "version": 1, "organization": "acme",
        "status_changed_at": datetime.now(UTC), "unknown_extra": "x",
    }  # fmt: skip
    request = AuthzRequest(
        subject=subject,
        action="invoices:transition:approve",
        resource_type="invoices",
        resource=record,
        input={},
        context={"action": "invoices:transition:approve", "now": datetime.now(UTC)},
        entity_id=7,
        id_field="id",
    )
    assert await engine.evaluate(request) is True


def test_cli(tmp_path, capsys):
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "invoices.cedar").write_text(GOOD)
    out = tmp_path / "schema.json"
    base = ["authz", "cedar-schema", "--app", "invoices.bootstrap:registry"]
    assert main([*base, "--entity-type", "invoices=Invoice", "--out", str(out)]) == 0
    assert "Invoice" in json.loads(out.read_text())[""]["entityTypes"]
    assert main([*base, "--entity-type", "invoices=Invoice", "--check", str(policies)]) == 0
    (policies / "broken.cedar").write_text(
        'permit(principal, action == Action::"invoices:nope", resource);'
    )
    capsys.readouterr()
    assert main([*base, "--entity-type", "invoices=Invoice", "--check", str(policies)]) == 1
    assert "unrecognized action" in capsys.readouterr().out
