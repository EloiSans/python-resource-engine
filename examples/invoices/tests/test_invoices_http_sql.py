"""End-to-end: FastAPI adapter + SQLAlchemy adapter (SQLite in memory)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from invoices.app import create_app
from invoices.devtokens import Tokens
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def tokens():
    return Tokens()


@pytest.fixture
def app(tokens):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return create_app(engine, authenticator=tokens.authenticator())


@pytest.fixture
def client(app, tokens):
    """An ACME user who issues and approves invoices."""
    with TestClient(app) as c:
        c.headers.update(tokens.headers("eli", ["issuer", "approver"], "acme"))
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

    r = client.post("/invoices/1/transitions/send")  # delivery: only approved invoices
    assert r.status_code == 409 and r.json()["error"]["code"] == "RE-E201"
    assert client.post("/invoices/1/transitions/submit").json()["status"] == "pending"
    r = client.patch("/invoices/1", json={"issue_date": "2026-12-01"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVOICE"

    r = client.post("/invoices/1/transitions/reject", json={"reason": "Wrong VAT"})
    assert r.json()["status"] == "rejected"
    assert r.json()["rejection_reason"] == "Wrong VAT"

    history = client.get("/invoices/1/history", params={"machine": "approval"}).json()
    assert [(h["event"], h["from_state"], h["to_state"]) for h in history] == [
        (None, None, "draft"),
        ("submit", "draft", "pending"),
        ("reject", "pending", "rejected"),
    ]
    both = client.get("/invoices/1/history").json()
    assert [(h["machine"], h["to_state"]) for h in both[:2]] == [
        ("approval", "draft"),
        ("delivery", "unsent"),
    ]
    assert client.get("/invoices/1/history", params={"machine": "x"}).status_code == 422
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

    related = client.get(f"/customers/{acme['id']}/invoices", params={"limit": 1}).json()
    assert related["total"] == 2 and len(related["items"]) == 1
    assert client.get("/customers/99/invoices").status_code == 404
    embedded = client.get(f"/customers/{acme['id']}", params={"expand": "invoices:1"}).json()
    assert embedded["invoices"]["total"] == 2 and embedded["invoices"]["has_more"] is True

    r = client.delete(f"/customers/{globex['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RE-E109"
    assert client.delete("/invoices/2").status_code == 204
    assert client.delete(f"/customers/{globex['id']}").status_code == 204

    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/invoices"]["get"]["parameters"]}
    assert {"expand", "customer_record.country", "customer_id"} <= params
    assert "/customers/{entity_id}/invoices" in spec["paths"]


def test_tags_many_to_many(client):
    acme = client.post("/customers", json={"name": "ACME", "tax_id": "B33333333"}).json()
    invoice = client.post(
        "/invoices", json={"customer": "x", "amount": "1", "customer_id": acme["id"]}
    ).json()
    urgent = client.post("/tags", json={"name": "urgent", "color": "red"}).json()
    review = client.post("/tags", json={"name": "review"}).json()
    for tag in (urgent, review):
        r = client.post("/invoice_tags", json={"invoice_id": invoice["id"], "tag_id": tag["id"]})
        assert r.status_code == 201, r.text
    again = client.post("/invoice_tags", json={"invoice_id": invoice["id"], "tag_id": urgent["id"]})
    assert again.status_code == 422 and again.json()["error"]["code"] == "INVOICE_TAG"

    tags = client.get(f"/invoices/{invoice['id']}/tags", params={"order_by": "name"}).json()
    assert [t["name"] for t in tags["items"]] == ["review", "urgent"]
    assert client.get(f"/tags/{urgent['id']}/invoices").json()["total"] == 1

    nested = client.get(
        f"/customers/{acme['id']}", params={"expand": "invoices.tags,invoices.customer_record"}
    ).json()
    first = nested["invoices"]["items"][0]
    assert first["tags"]["total"] == 2 and first["customer_record"]["name"] == "ACME"

    assert client.delete(f"/tags/{urgent['id']}").status_code == 204  # cascades
    assert client.get(f"/invoices/{invoice['id']}/tags").json()["total"] == 1


def test_every_request_needs_a_token(app):
    with TestClient(app) as anonymous:
        r = anonymous.get("/invoices")
        assert r.status_code == 401 and r.json()["error"]["code"] == "RE-E701"
        bad = anonymous.get("/invoices", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


def test_organizations_are_isolated(app, tokens):
    acme = tokens.headers("eli", ["issuer"], "acme")
    globex = tokens.headers("gina", ["issuer"], "globex")
    nobody = tokens.headers("lost", ["issuer"])  # no organization in the token
    with TestClient(app) as c:
        mine = c.post("/invoices", json={"customer": "ACME", "amount": "5"}, headers=acme).json()
        assert mine["organization"] == "acme"
        theirs = c.post("/invoices", json={"customer": "G", "amount": "7"}, headers=globex).json()
        assert theirs["organization"] == "globex"

        assert [i["id"] for i in c.get("/invoices", headers=acme).json()["items"]] == [mine["id"]]
        assert c.get(f"/invoices/{theirs['id']}", headers=acme).status_code == 404
        stolen = c.post(
            "/invoices",
            json={"customer": "x", "amount": "1", "customer_id": None},
            headers=acme,
            params={"tenant": "globex"},
        )
        assert stolen.status_code == 403 and stolen.json()["error"]["code"] == "RE-E709"
        customer = c.post("/customers", json={"name": "G", "tax_id": "B777"}, headers=globex)
        cross = c.post(
            "/invoices",
            json={"customer": "x", "amount": "1", "customer_id": customer.json()["id"]},
            headers=acme,
        )
        assert cross.json()["error"]["code"] == "RE-E108"  # another organization's customer
        assert c.get("/invoices", headers=nobody).json()["error"]["code"] == "RE-E708"

        admin = tokens.headers("root", ["invoices-admin"])  # cross-tenant system role
        everyone = c.get("/invoices", headers=admin).json()
        assert everyone["total"] == 2
        only = c.get("/invoices", headers=admin, params={"tenant": "globex"}).json()
        assert [i["organization"] for i in only["items"]] == ["globex"]
        need = c.post("/tags", json={"name": "x"}, headers=admin)
        assert need.json()["error"]["code"] == "RE-E708"  # admins name the organization
        made = c.post("/tags", json={"name": "x"}, headers=admin, params={"tenant": "globex"})
        assert made.json()["organization"] == "globex"


def test_roles_from_the_token(app, tokens):
    accountant = tokens.headers("ada", ["accountant"], "acme")
    issuer = tokens.headers("eli", ["issuer"], "acme")
    with TestClient(app) as c:
        invoice = c.post("/invoices", json={"customer": "x", "amount": "1"}, headers=issuer)
        assert invoice.status_code == 201
        assert c.get("/invoices", headers=accountant).json()["total"] == 1
        denied = c.post("/invoices", json={"customer": "x", "amount": "1"}, headers=accountant)
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "RE-E702"
        me = c.get("/authz/me", headers=accountant).json()
        assert me["roles"] == ["viewer"] and me["actions"]["invoices:list"] == "allowed"


def test_processes_are_read_only_over_http(client):
    spec = client.get("/openapi.json").json()["paths"]
    assert set(spec["/invoice_payments"]) == {"get"}  # no POST: the process creates them
    assert not [p for p in spec if p.startswith("/invoice_payments/{entity_id}/transitions")]
    assert client.get("/invoice_payments").json()["total"] == 0


def test_customer_balances_view(app, tokens):
    acme = tokens.headers("eli", ["issuer", "approver"], "acme")
    globex = tokens.headers("gina", ["issuer"], "globex")
    with TestClient(app) as c:
        customer = c.post("/customers", json={"name": "Bar", "tax_id": "B123"}, headers=acme)
        cid = customer.json()["id"]
        for amount in ("100.50", "20"):
            r = c.post(
                "/invoices",
                json={"customer": "Bar", "amount": amount, "customer_id": cid},
                headers=acme,
            )
            invoice = r.json()["id"]
        c.post(f"/invoices/{invoice}/transitions/submit", headers=acme)
        other = c.post("/customers", json={"name": "G", "tax_id": "B999"}, headers=globex)
        c.post(
            "/invoices",
            json={"customer": "G", "amount": "7", "customer_id": other.json()["id"]},
            headers=globex,
        )

        page = c.get("/customer_balances", headers=acme).json()  # one GROUP BY query
        assert page["total"] == 1  # only the caller's organization
        [row] = page["items"]
        assert (row["customer_id"], row["invoices"], row["organization"]) == (cid, 2, "acme")
        assert (row["total"], row["outstanding"], row["paid"]) == ("120.50", "20.00", "0.00")
        one = c.get(f"/customer_balances/{cid}", headers=acme, params={"expand": "customer"})
        assert one.json()["customer"]["name"] == "Bar"
        assert c.get(f"/customer_balances/{other.json()['id']}", headers=acme).status_code == 404
        assert c.post("/customer_balances", json={}, headers=acme).status_code == 405
        ranked = c.get("/customer_balances", headers=globex, params={"order_by": "-total"})
        assert ranked.json()["items"][0]["total"] == "7.00"
