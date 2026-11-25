"""``scaffold consumer`` and the contract checks between services."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from resource_engine.messaging import EventEnvelope
from resource_engine_devtools.cli import main
from resource_engine_devtools.consumer_scaffold import consumer, provider_messages
from resource_engine_devtools.contracts import (
    assert_consumer_contract,
    breaking_changes,
    consumer_problems,
)
from resource_engine_devtools.scaffold import ScaffoldError

REPO = Path(__file__).resolve().parents[3]
INVOICES = REPO / "examples" / "invoices" / "docs" / "asyncapi.json"

#: A provider that is not a Resource Engine app: CloudEvents, refs, raw bodies.
PAYMENTS = {
    "asyncapi": "3.0.0",
    "info": {"title": "Payments", "version": "2.1.0"},
    "channels": {
        "payments": {
            "address": "payments.events",
            "messages": {
                "PaymentReceived": {"$ref": "#/components/messages/PaymentReceived"},
                "RefundIssued": {"$ref": "#/components/messages/RefundIssued"},
            },
        }
    },
    "operations": {
        "publishPayments": {
            "action": "send",
            "channel": {"$ref": "#/channels/payments"},
            "messages": [
                {"$ref": "#/channels/payments/messages/PaymentReceived"},
                {"$ref": "#/channels/payments/messages/RefundIssued"},
            ],
        }
    },
    "components": {
        "messages": {
            "PaymentReceived": {
                "name": "PaymentReceived",
                "summary": "A card or transfer payment was collected.",
                "payload": {
                    "type": "object",
                    "required": ["specversion", "id", "type", "data"],
                    "properties": {
                        "specversion": {"const": "1.0"},
                        "id": {"type": "string"},
                        "type": {"const": "com.acme.payments.PaymentReceived"},
                        "data": {"$ref": "#/components/schemas/Payment"},
                    },
                },
                "examples": [
                    {
                        "payload": {
                            "data": {
                                "invoice-id": 7,
                                "amount": "12.50",
                                "method": "card",
                                "paid_at": "2026-11-25T10:00:00+00:00",
                                "payer": {"name": "ACME", "country": "ES"},
                                "tags": ["web"],
                            }
                        }
                    }
                ],
            },
            "RefundIssued": {
                "name": "RefundIssued",
                "payload": {
                    "type": "object",
                    "required": ["refund_id", "amount"],
                    "properties": {
                        "refund_id": {"type": "string", "format": "uuid"},
                        "amount": {"type": "number", "minimum": 0},
                        "reason": {"type": ["string", "null"]},
                    },
                },
            },
        },
        "schemas": {
            "Payment": {
                "type": "object",
                "description": "A collected payment.",
                "required": ["invoice-id", "amount", "method", "paid_at"],
                "properties": {
                    "invoice-id": {"type": "integer", "description": "The invoice paid"},
                    "amount": {"type": "string"},
                    "method": {"enum": ["card", "transfer"]},
                    "paid_at": {"type": "string", "format": "date-time"},
                    "payer": {"$ref": "#/components/schemas/Payer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            },
            "Payer": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "country": {"type": "string"}},
            },
        },
    },
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [m for m in sys.modules if m.split(".")[0] in ("scafbill", "scafpay")]:
        del sys.modules[name]


def _write_doc(path: Path, doc: dict) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _clean(project: Path, base: Path, passed: str) -> None:
    ruff = [sys.executable, "-m", "ruff"]
    config = ["--config", str(REPO / "pyproject.toml")]
    assert subprocess.run([*ruff, "check", *config, str(project)]).returncode == 0
    assert subprocess.run([*ruff, "format", "--check", *config, str(project)]).returncode == 0
    env = os.environ | {"PYTHONPATH": str(project)}
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o",
         "asyncio_mode=auto", str(base / "tests")],
        cwd=project, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert passed in tests.stdout


# ------------------------------------------------------------------- scaffold


def test_consumer_of_a_resource_engine_app(project, capsys):
    args = [
        "scaffold", "consumer", "invoice_events", "--package", "scafbill.billing",
        "--root", str(project), "--asyncapi", str(INVOICES), "--channel", "invoices",
    ]  # fmt: skip
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "scafbill.billing.consumers.invoice_events:build_router" in out
    (project / "scafbill" / "__init__.py").write_text("")
    (project / "scafbill" / "billing" / "__init__.py").write_text("")
    base = project / "scafbill" / "billing"
    assert (base / "tests" / "contracts" / "invoice_events.asyncapi.json").is_file()
    module = importlib.import_module("scafbill.billing.consumers.invoice_events")
    router = module.build_router()
    assert "TransitionExecuted" in router.event_types
    transition = router.payload_type("TransitionExecuted")
    assert set(transition.model_fields) == {
        "resource",
        "entity_id",
        "transition",
        "source",
        "target",
    }
    n = len(router.event_types)
    _clean(project, base, f"{2 * n + 1} passed")


def test_consumer_of_another_provider(project, capsys):
    source = _write_doc(project / "payments.json", PAYMENTS)
    result = consumer(
        project, "scafpay.ledger", "payments", source, type_prefix="com.acme.payments."
    )
    (project / "scafpay" / "__init__.py").write_text("")
    (project / "scafpay" / "ledger" / "__init__.py").write_text("")
    assert any("decoder" in step and "RefundIssued" in step for step in result.next_steps)
    module = importlib.import_module("scafpay.ledger.consumers.payments")
    router = module.build_router()
    assert router.event_types == ["PaymentReceived", "RefundIssued"]
    payment = router.parse(
        EventEnvelope(
            type="PaymentReceived",
            payload=PAYMENTS["components"]["messages"]["PaymentReceived"]["examples"][0]["payload"][
                "data"
            ],
            occurred_at=datetime.now(UTC),
        )
    )
    assert payment.invoice_id == 7  # "invoice-id", by alias
    assert payment.method == "card" and payment.payer.name == "ACME"
    assert payment.paid_at.year == 2026
    source_code = (project / "scafpay" / "ledger" / "consumers" / "payments.py").read_text()
    assert 'method: Literal["card", "transfer"]' in source_code
    assert "class Payer(BaseModel):" in source_code
    assert "refund_id: UUID" in source_code
    assert "metadata: dict[str, str] | None" in source_code
    _clean(project, project / "scafpay" / "ledger", "5 passed")


def test_asyncapi_2(project):
    doc = {
        "asyncapi": "2.6.0",
        "info": {"title": "Old", "version": "1"},
        "channels": {
            "orders": {
                "subscribe": {
                    "message": {
                        "oneOf": [
                            {
                                "name": "OrderShipped",
                                "payload": {
                                    "type": "object",
                                    "properties": {"order_id": {"type": "integer"}},
                                },
                            },
                            {"name": "OrderLost", "payload": {"type": "object"}},
                        ]
                    }
                }
            }
        },
    }
    found = provider_messages(doc)
    assert [(m.event_type, m.channel, m.wire) for m in found] == [
        ("OrderShipped", "orders", "raw"),
        ("OrderLost", "orders", "raw"),
    ]


def test_bad_input(project):
    with pytest.raises(ScaffoldError, match="No AsyncAPI document"):
        consumer(project, "x.y", "payments", project / "missing.json")
    with pytest.raises(ScaffoldError, match="not an AsyncAPI"):
        consumer(project, "x.y", "payments", _write_doc(project / "a.json", {"openapi": "3"}))
    source = _write_doc(project / "p.json", PAYMENTS)
    with pytest.raises(ScaffoldError, match="No messages"):
        consumer(project, "x.y", "payments", source, messages=["Nope"])
    with pytest.raises(ScaffoldError, match="Invalid consumer"):
        consumer(project, "x.y", "Payments", source)


# ------------------------------------------------------------------ contracts


def _payments_router(project):
    source = _write_doc(project / "payments.json", PAYMENTS)
    consumer(project, "scafpay.ledger", "payments", source, type_prefix="com.acme.payments.")
    (project / "scafpay" / "__init__.py").write_text("")
    (project / "scafpay" / "ledger" / "__init__.py").write_text("")
    return importlib.import_module("scafpay.ledger.consumers.payments").build_router()


def test_consumer_contract(project):
    router = _payments_router(project)
    prefix = "com.acme.payments."
    assert consumer_problems(router, PAYMENTS, type_prefix=prefix) == []

    changed = copy.deepcopy(PAYMENTS)
    payment = changed["components"]["schemas"]["Payment"]
    payment["required"].remove("paid_at")  # now optional for the provider
    payment["properties"]["method"]["enum"].append("crypto")  # a value we cannot read
    payment["properties"]["invoice-id"] = {"type": "string"}  # a different type
    del changed["components"]["messages"]["RefundIssued"]  # no longer sent
    del changed["channels"]["payments"]["messages"]["RefundIssued"]
    changed["operations"]["publishPayments"]["messages"].pop()
    problems = {str(p) for p in consumer_problems(router, changed, type_prefix=prefix)}
    assert "PaymentReceived.paid_at: required by the reader but optional for the writer" in problems
    assert 'PaymentReceived.method: new values "crypto"' in problems
    assert "PaymentReceived.invoice-id: sent as string; expected integer" in problems
    assert "RefundIssued: handled here, but the provider no longer sends it" in problems
    with pytest.raises(AssertionError, match="no longer matches"):
        assert_consumer_contract(router, changed, type_prefix=prefix)


def test_breaking_changes_of_a_published_document():
    old = json.loads(INVOICES.read_text())
    assert breaking_changes(old, old) == []

    new = copy.deepcopy(old)
    schemas = new["components"]["schemas"]
    transition = schemas["invoices.TransitionExecuted"]["properties"]["payload"]["properties"]
    transition["transition"]["enum"].append("archive")
    created = schemas["invoices.ResourceCreated"]["properties"]["payload"]
    created["properties"]["entity_id"] = {"type": ["integer", "null"]}
    recalculated = schemas["invoices.InvoiceTotalsRecalculated"]["properties"]["payload"]
    del recalculated["properties"]["new_amount"]
    recalculated["properties"]["currency"] = {"type": "string"}  # added: compatible
    channel = new["channels"]["invoices"]["messages"]
    del channel["ActionExecuted"]
    new["operations"]["publish.invoices"]["messages"] = [
        m for m in new["operations"]["publish.invoices"]["messages"]
        if not m["$ref"].endswith("/ActionExecuted")
    ]  # fmt: skip
    found = {str(p) for p in breaking_changes(old, new)}
    assert found == {
        'TransitionExecuted.transition: new values "archive"',
        "ResourceCreated.entity_id: sent as null; expected integer",
        "InvoiceTotalsRecalculated.new_amount: removed",
        "ActionExecuted: no longer published on invoices",
    }


def test_check_command(tmp_path, monkeypatch, capsys):
    published = json.loads(INVOICES.read_text())
    payload = published["components"]["schemas"]["invoices.ResourceCreated"]["properties"]
    payload["payload"]["properties"]["legacy_code"] = {"type": "string"}
    payload["payload"]["required"].append("legacy_code")
    path = _write_doc(tmp_path / "published.json", published)
    monkeypatch.chdir(REPO)
    assert main(["docs", "asyncapi", "--check", str(path)]) == 1
    assert "BREAKING ResourceCreated.legacy_code: removed" in capsys.readouterr().out
    assert main(["docs", "asyncapi", "--check", str(INVOICES), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"breaking": [], "changed": False}
