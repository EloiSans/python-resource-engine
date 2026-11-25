"""``mount_asyncapi`` serves the AsyncAPI document next to OpenAPI."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from invoices.bootstrap import registry
from resource_engine.messaging import EventRouter
from resource_engine_fastapi import mount_asyncapi


def test_served_with_the_app_title_and_version():
    app = FastAPI(title="Invoices", version="3.1.0")
    router = EventRouter(name="payments")
    router.register("PaymentReceived", lambda envelope: None)
    mount_asyncapi(app, registry, consumers=[router], format="cloudevents")
    with TestClient(app) as client:
        doc = client.get("/asyncapi.json").json()
        assert "/asyncapi.json" not in client.get("/openapi.json").json()["paths"]
    assert doc["info"] == {"title": "Invoices", "version": "3.1.0"}
    assert {"invoices", "payments"} <= set(doc["channels"])


def test_a_prebuilt_document():
    app = FastAPI()
    mount_asyncapi(app, registry, path="/events/asyncapi.json", document={"asyncapi": "3.0.0"})
    with TestClient(app) as client:
        assert client.get("/events/asyncapi.json").json() == {"asyncapi": "3.0.0"}
