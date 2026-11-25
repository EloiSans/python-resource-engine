"""AsyncAPI document, declared domain events and typed consumer payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from invoices.bootstrap import registry as invoices
from invoices.domain.events import InvoiceTotalsRecalculated
from pydantic import BaseModel, ConfigDict
from resource_engine import DomainEvent, Resource, ResourceRegistry
from resource_engine.errors import ConfigurationError, MessageRejected
from resource_engine.messaging import EventEnvelope, EventRouter
from resource_engine.messaging.asyncapi import asyncapi_document


@dataclass(frozen=True, kw_only=True)
class PaymentReceived(DomainEvent):
    """The payments service collected an invoice."""

    invoice_id: int
    amount: Decimal


class ShipmentSent(BaseModel):
    order_id: int
    carrier: str


def _resolve(document: dict[str, Any], ref: str) -> Any:
    assert ref.startswith("#/"), ref
    node: Any = document
    for part in ref[2:].split("/"):
        assert part in node, f"dangling $ref {ref}"
        node = node[part]
    return node


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []


def _payments() -> EventRouter:
    router = EventRouter(name="invoices-payments", description="Payments collected elsewhere.")

    @router.on("PaymentReceived", payload=PaymentReceived)
    async def mark_paid(envelope, payment):
        """Marks the invoice as paid."""

    router.register("Ping", lambda envelope: None)
    return router


# ---------------------------------------------------------------- document


def test_published_events_per_resource():
    doc = asyncapi_document(invoices, title="Invoices", version="2.0.0")
    assert doc["asyncapi"] == "3.0.0"
    assert doc["info"] == {"title": "Invoices", "version": "2.0.0"}
    channel = doc["channels"]["invoices"]
    assert channel["address"] == "invoices"
    assert set(channel["messages"]) == {
        "ResourceCreated",
        "ResourceUpdated",
        "ResourceDeleted",
        "TransitionExecuted",
        "ActionExecuted",
        "DocumentAttached",
        "DocumentDetached",
        "InvoiceTotalsRecalculated",
    }
    operation = doc["operations"]["publish.invoices"]
    assert operation["action"] == "send"
    assert operation["channel"] == {"$ref": "#/channels/invoices"}
    # resources without events (read-only ones) have no channel
    assert "audit_log" not in doc["channels"]
    assert "documents" not in doc["channels"]
    for ref in _refs(doc):
        _resolve(doc, ref)


def test_messages_are_typed_for_the_resource():
    doc = asyncapi_document(invoices)
    schemas = doc["components"]["schemas"]
    transition = schemas["invoices.TransitionExecuted"]
    assert transition["properties"]["type"] == {"const": "TransitionExecuted"}
    assert transition["properties"]["resource"] == {"const": "invoices"}
    payload = transition["properties"]["payload"]["properties"]
    assert payload["entity_id"] == {"type": "integer"}
    assert payload["transition"]["enum"] == ["submit", "approve", "reject", "pay", "reopen"]
    assert "pending" in payload["source"]["enum"]

    action = schemas["invoices.ActionExecuted"]["properties"]["payload"]["properties"]
    assert action["action"]["enum"] == ["recalculate_totals", "render_summary"]

    domain = schemas["invoices.InvoiceTotalsRecalculated"]["properties"]["payload"]
    assert set(domain["properties"]) == {"invoice_id", "previous_amount", "new_amount"}
    assert domain["properties"]["new_amount"]["type"] == "string"  # Decimal on the wire
    assert "occurred_at" not in domain["properties"]

    message = doc["components"]["messages"]["invoices.InvoiceTotalsRecalculated"]
    assert message["summary"].startswith("L'import d'una factura")
    assert message["contentType"] == "application/json"
    assert message["correlationId"] == {"location": "$message.payload#/correlation_id"}
    assert message["headers"]["properties"]["event_type"] == {"const": "InvoiceTotalsRecalculated"}


def test_consumed_events():
    doc = asyncapi_document(invoices, resources=["invoices"], consumers=[_payments()])
    channel = doc["channels"]["invoices-payments"]
    assert channel["description"] == "Payments collected elsewhere."
    assert set(channel["messages"]) == {"PaymentReceived", "Ping"}
    assert doc["operations"]["receive.invoices-payments"]["action"] == "receive"
    schemas = doc["components"]["schemas"]
    typed = schemas["invoices-payments.PaymentReceived"]["properties"]["payload"]
    assert set(typed["properties"]) == {"invoice_id", "amount"}
    untyped = schemas["invoices-payments.Ping"]["properties"]["payload"]
    assert untyped["type"] == "object" and "properties" not in untyped
    message = doc["components"]["messages"]["invoices-payments.PaymentReceived"]
    assert message["summary"] == "Marks the invoice as paid."
    for ref in _refs(doc):
        _resolve(doc, ref)


def test_cloudevents_format_servers_and_channels():
    doc = asyncapi_document(
        invoices,
        resources=["invoices"],
        format="cloudevents",
        source="/invoices-service",
        type_prefix="com.example.invoices.",
        servers={"mesh": {"host": "mesh.example.com", "protocol": "https"}},
        channels={
            "invoices": {
                "address": "default/sap.s4/1/invoices",
                "servers": ["mesh"],
                "bindings": {"http": {"method": "POST"}},
            }
        },
    )
    channel = doc["channels"]["invoices"]
    assert channel["address"] == "default/sap.s4/1/invoices"
    assert channel["servers"] == [{"$ref": "#/servers/mesh"}]
    assert channel["bindings"] == {"http": {"method": "POST"}}
    event = doc["components"]["schemas"]["invoices.ResourceCreated"]
    assert event["properties"]["type"] == {"const": "com.example.invoices.ResourceCreated"}
    assert event["properties"]["source"] == {"const": "/invoices-service"}
    assert "data" in event["required"]
    message = doc["components"]["messages"]["invoices.ResourceCreated"]
    assert message["contentType"].startswith("application/cloudevents+json")
    assert "headers" not in message
    for ref in _refs(doc):
        _resolve(doc, ref)


def test_configuration_errors():
    with pytest.raises(ConfigurationError, match="host and a protocol"):
        asyncapi_document(invoices, servers={"broken": {"host": "x"}})
    with pytest.raises(ConfigurationError, match="unknown servers"):
        asyncapi_document(invoices, channels={"invoices": {"servers": ["nowhere"]}})
    with pytest.raises(ConfigurationError, match="both a resource and a consumer"):
        asyncapi_document(invoices, consumers=[EventRouter(name="invoices")])
    with pytest.raises(ConfigurationError, match="format"):
        asyncapi_document(invoices, channels={"invoices": {"format": "avro"}})


# ---------------------------------------------------------- declared events


@dataclass
class Order:
    id: int | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int


def _resource(events):
    class OrderResource(Resource):
        name = "orders"
        model = Order
        schemas = {"read": OrderOut}
        operations = ["get"]

    OrderResource.events = events
    return OrderResource


def test_declared_events_are_validated():
    assert _resource([InvoiceTotalsRecalculated]).definition().events == (
        InvoiceTotalsRecalculated,
    )
    with pytest.raises(ConfigurationError, match="DomainEvent dataclasses"):
        _resource([ShipmentSent]).definition()

    @dataclass(frozen=True, kw_only=True)
    class InvoiceTotalsRecalculated2(DomainEvent):
        pass

    InvoiceTotalsRecalculated2.__name__ = "InvoiceTotalsRecalculated"
    with pytest.raises(ConfigurationError, match="share a class name"):
        _resource([InvoiceTotalsRecalculated, InvoiceTotalsRecalculated2]).definition()


def test_a_declared_event_named_like_an_engine_event_is_refused():
    @dataclass(frozen=True, kw_only=True)
    class ResourceCreated(DomainEvent):
        pass

    registry = ResourceRegistry()
    resource = _resource([ResourceCreated])
    resource.operations = ["get", "create"]
    resource.schemas = {"read": OrderOut, "create": ShipmentSent}
    registry.register(resource)
    with pytest.raises(ConfigurationError, match="name of an engine event"):
        asyncapi_document(registry)


# ------------------------------------------------------------ typed payloads


def _envelope(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(type=event_type, payload=payload, occurred_at=datetime.now(UTC))


async def test_typed_handlers_get_the_parsed_payload():
    router = EventRouter()
    seen: list[Any] = []

    @router.on("PaymentReceived", payload=PaymentReceived)
    async def paid(envelope, payment):
        seen.append(payment)

    @router.on("ShipmentSent", payload=ShipmentSent)
    def shipped(envelope, shipment):
        seen.append(shipment)

    @router.on("ShipmentSent")
    def untyped(envelope):
        seen.append(envelope.payload)

    await router.dispatch(_envelope("PaymentReceived", {"invoice_id": 7, "amount": "12.50"}))
    await router.dispatch(_envelope("ShipmentSent", {"order_id": 1, "carrier": "UPS"}))
    payment, shipment, raw = seen
    assert payment == PaymentReceived(
        invoice_id=7, amount=Decimal("12.50"), occurred_at=payment.occurred_at
    )
    assert shipment == ShipmentSent(order_id=1, carrier="UPS")
    assert raw == {"order_id": 1, "carrier": "UPS"}
    assert router.payload_type("ShipmentSent") is ShipmentSent


async def test_invalid_payloads_are_rejected():
    router = EventRouter()
    router.register("ShipmentSent", lambda envelope, shipment: None, payload=ShipmentSent)
    with pytest.raises(MessageRejected) as caught:
        await router.dispatch(_envelope("ShipmentSent", {"order_id": "x"}))
    assert caught.value.code == "RE-E406"
    assert {e["loc"][0] for e in caught.value.details} == {"order_id", "carrier"}


def test_one_payload_type_per_event_type():
    router = EventRouter()
    router.register("ShipmentSent", lambda e, s: None, payload=ShipmentSent)
    router.register("ShipmentSent", lambda e, s: None, payload=ShipmentSent)
    with pytest.raises(ConfigurationError, match="two payload types"):
        router.register("ShipmentSent", lambda e, s: None, payload=PaymentReceived)
