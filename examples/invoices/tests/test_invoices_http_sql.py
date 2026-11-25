"""End-to-end: FastAPI adapter + SQLAlchemy adapter (SQLite in memory)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from invoices.app import create_app
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with TestClient(create_app(engine)) as c:
        yield c


def test_crud_and_transitions_over_http(client):
    r = client.post("/invoices", json={"customer": "ACME", "amount": "120.50"})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["status"] == "draft" and inv["id"] == 1

    assert client.get("/invoices/1/transitions").json() == ["submit"]
    r = client.post("/invoices/1/transitions/pay")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "RE-E201"

    assert client.post("/invoices/1/transitions/submit").json()["status"] == "pending"
    r = client.patch("/invoices/1", json={"notes": "late"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVOICE"

    r = client.post("/invoices/1/transitions/reject", json={"reason": "Wrong VAT"})
    assert r.json()["status"] == "rejected"
    assert r.json()["rejection_reason"] == "Wrong VAT"

    history = client.get("/invoices/1/history").json()
    assert [(h["event"], h["from_state"], h["to_state"]) for h in history] == [
        (None, None, "draft"),
        ("submit", "draft", "pending"),
        ("reject", "pending", "rejected"),
    ]
    assert client.get("/invoices/99/history").status_code == 404


def test_validation_list_and_delete(client):
    assert client.post("/invoices", json={"customer": ""}).status_code == 422
    for customer, amount in [("A", "30"), ("B", "10"), ("A", "20")]:
        client.post("/invoices", json={"customer": customer, "amount": amount})
    r = client.get("/invoices", params={"customer": "A", "order_by": "-amount"})
    body = r.json()
    assert body["total"] == 2
    assert [i["amount"] for i in body["items"]] == ["30.00", "20.00"]
    assert client.get("/invoices", params={"order_by": "notes"}).status_code == 400

    assert client.delete("/invoices/2").status_code == 204
    r = client.get("/invoices/2")
    assert r.status_code == 404 and r.json()["error"]["code"] == "RE-E102"


def test_action_endpoint_and_rollback(client):
    client.post("/invoices", json={"customer": "ACME", "amount": "200"})
    r = client.post("/invoices/1/actions/recalculate_totals", json={"percent": "10"})
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == "180.00"
    # a failing after_update hook rolls the SQL transaction back
    assert client.patch("/invoices/1", json={"amount": "-1"}).status_code == 422
    assert client.get("/invoices/1").json()["amount"] == "180.00"


def test_openapi_contains_generated_operations(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/invoices",
        "/invoices/{entity_id}",
        "/invoices/{entity_id}/transitions/approve",
        "/invoices/{entity_id}/actions/recalculate_totals",
    } <= set(paths)
    assert "requestBody" in paths["/invoices/{entity_id}/transitions/reject"]["post"]
    assert "requestBody" not in paths["/invoices/{entity_id}/transitions/approve"]["post"]


def test_concurrent_edits_are_refused(client):
    r = client.post("/invoices", json={"customer": "ACME", "amount": "100"})
    etag = r.headers["etag"]
    assert etag == '"1"' and r.json()["version"] == 1
    # two people opened version 1; the first one saves
    first = client.patch("/invoices/1", json={"amount": "110"}, headers={"If-Match": etag})
    assert first.status_code == 200 and first.headers["etag"] == '"2"'
    second = client.patch("/invoices/1", json={"amount": "90"}, headers={"If-Match": etag})
    assert second.status_code == 409 and second.json()["error"]["code"] == "RE-E105"
    assert client.get("/invoices/1").json()["amount"] == "110.00"
    stale = client.post("/invoices/1/transitions/submit", headers={"If-Match": etag})
    assert stale.status_code == 409
    fresh = client.post("/invoices/1/transitions/submit", headers={"If-Match": '"2"'})
    assert fresh.status_code == 200 and fresh.json()["version"] == 3
    assert client.delete("/invoices/1", headers={"If-Match": etag}).status_code == 409


def test_idempotency_key(client):
    body = {"customer": "ACME", "amount": "75"}
    first = client.post("/invoices", json=body, headers={"Idempotency-Key": "order-7"})
    again = client.post("/invoices", json=body, headers={"Idempotency-Key": "order-7"})
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert client.get("/invoices").json()["total"] == 1
    other = client.post(
        "/invoices", json={**body, "amount": "76"}, headers={"Idempotency-Key": "order-7"}
    )
    assert other.status_code == 422 and other.json()["error"]["code"] == "RE-E107"


def test_customers_and_references(client):
    acme = client.post("/customers", json={"name": "ACME", "tax_id": "B11111111"}).json()
    globex = client.post(
        "/customers", json={"name": "Globex", "tax_id": "B22222222", "country": "FR"}
    ).json()
    for customer in (acme, globex, acme):
        r = client.post(
            "/invoices", json={"customer": "x", "amount": "1", "customer_id": customer["id"]}
        )
        assert r.status_code == 201, r.text

    r = client.post("/invoices", json={"customer": "x", "amount": "1", "customer_id": 99})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RE-E108"

    one = client.get("/invoices/1", params={"expand": "customer_record"}).json()
    assert one["customer_record"]["name"] == "ACME"
    assert "customer_record" not in client.get("/invoices/1").json()

    page = client.get(
        "/invoices", params={"customer_record.country": "FR", "expand": "customer_record"}
    ).json()
    assert [i["customer_record"]["name"] for i in page["items"]] == ["Globex"]
    assert client.get("/invoices", params={"expand": "vendor"}).status_code == 400

    r = client.delete(f"/customers/{globex['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RE-E109"
    assert client.delete("/invoices/2").status_code == 204
    assert client.delete(f"/customers/{globex['id']}").status_code == 204

    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/invoices"]["get"]["parameters"]}
    assert {"expand", "customer_record.country", "customer_id"} <= params
