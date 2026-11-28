"""Event versions published side by side, their AsyncAPI messages, consumers
that pick one, and the contract checks comparing versions (AsyncAPI, OpenAPI)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from resource_engine import Resource, ResourceRegistry
from resource_engine.domain.events import DomainEvent
from resource_engine.messaging import EventRouter
from resource_engine.messaging.asyncapi import asyncapi_document
from resource_engine.messaging.cloudevents import from_cloudevent, to_cloudevent
from resource_engine.messaging.envelope import EventEnvelope, EventSerializer
from resource_engine.versions import Deprecation, EventVersion
from resource_engine_devtools.contracts import breaking_changes, openapi_breaking_changes

PAST = Deprecation(since=date(2025, 1, 1), sunset=date(2025, 6, 30))
FUTURE = Deprecation(sunset=date(2099, 1, 1), reason="Use v2: totals are decimals.")


@dataclass(frozen=True, kw_only=True)
class InvoiceApprovedV1(DomainEvent):
    invoice_id: int
    amount: str


@dataclass(frozen=True, kw_only=True)
class InvoiceApproved(DomainEvent):
    """An invoice was approved."""

    schema_version = 2
    previous_versions = {
        1: EventVersion(
            InvoiceApprovedV1,
            lambda p: {"invoice_id": p["invoice_id"], "amount": p["total"]},
            deprecation=FUTURE,
        )
    }
    invoice_id: int
    total: Decimal


@dataclass
class Invoice:
    number: str
    id: int | None = None


class InvoiceIn(BaseModel):
    number: str


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str


def registry_with(*events) -> ResourceRegistry:
    class InvoiceResource(Resource):
        name = "invoices"
        model = Invoice
        schemas = {"create": InvoiceIn, "read": InvoiceOut}
        operations = ["get"]

    InvoiceResource.events = list(events)
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    return registry


def test_older_versions_are_published_side_by_side():
    envelopes = EventSerializer().to_envelopes(
        InvoiceApproved(invoice_id=7, total=Decimal("12.50")), resource="invoices"
    )
    current, older = envelopes
    assert current.schema_version == "2" and current.payload == {"invoice_id": 7, "total": "12.50"}
    assert older.schema_version == "1" and older.payload == {"invoice_id": 7, "amount": "12.50"}
    assert older.id == f"{current.id}.v1" and older.key == current.key
    assert older.attributes()["schema_version"] == "1"
    assert EventEnvelope.from_json(older.to_json()).schema_version == "1"
    assert from_cloudevent(to_cloudevent(current, source="/x")).schema_version == "2"


async def test_consumers_pick_their_version():
    seen = []
    router = EventRouter()
    router.on("InvoiceApproved", payload=InvoiceApprovedV1, schema_version=1)(
        lambda envelope, payload: seen.append(payload.amount)
    )
    for envelope in EventSerializer().to_envelopes(
        InvoiceApproved(invoice_id=7, total=Decimal("12.50"))
    ):
        await router.dispatch(envelope)
    assert seen == ["12.50"]
    assert router.schema_version("InvoiceApproved") == "1"


def test_asyncapi_lists_each_version_with_its_deprecation():
    document = asyncapi_document(registry_with(InvoiceApproved))
    messages = document["components"]["messages"]
    current = messages["invoices.InvoiceApproved.v2"]
    older = messages["invoices.InvoiceApproved"]
    assert current["title"] == "InvoiceApproved v2"
    schemas = document["components"]["schemas"]
    assert schemas["invoices.InvoiceApproved.v2"]["properties"]["schema_version"] == {"const": "2"}
    assert schemas["invoices.InvoiceApproved"]["properties"]["schema_version"] == {"const": "1"}
    assert schemas["invoices.InvoiceApproved"]["deprecated"] is True
    assert older["x-deprecation"] == {"sunset": "2099-01-01", "reason": FUTURE.reason}
    assert older["description"].startswith("Deprecated; removed on 2099-01-01.")


def test_breaking_changes_compare_each_version():
    @dataclass(frozen=True, kw_only=True)
    class Before(DomainEvent):
        invoice_id: int
        amount: str

    Before.__name__ = "InvoiceApproved"
    old = asyncapi_document(registry_with(Before))
    new = asyncapi_document(registry_with(InvoiceApproved))
    assert breaking_changes(old, new) == []  # v1 still published; v2 is new

    @dataclass(frozen=True, kw_only=True)
    class Dropped(DomainEvent):
        schema_version = 2
        invoice_id: int
        total: Decimal

    Dropped.__name__ = "InvoiceApproved"
    gone = asyncapi_document(registry_with(Dropped))
    problems = [str(p) for p in breaking_changes(new, gone)]
    assert problems == [
        "InvoiceApproved: no longer published on invoices (its sunset is 2099-01-01)"
    ]
    retired = copy.deepcopy(new)
    retired["components"]["messages"]["invoices.InvoiceApproved"]["x-deprecation"] = PAST.to_dict()
    assert breaking_changes(retired, gone) == []  # its sunset has passed


def test_openapi_breaking_changes():
    def spec(*, required=("number",), fields=("id", "number"), deprecated=None, extra_path=True):
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "number": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": list(required),
                        }
                    }
                }
            },
            "responses": {
                "201": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {f: {"type": "string"} for f in fields},
                                "required": list(fields),
                            }
                        }
                    }
                }
            },
        }
        paths = {"/v2/invoices": {"post": operation}}
        if extra_path:
            old = copy.deepcopy(operation)
            if deprecated:
                old |= {"deprecated": True, "x-deprecation": deprecated.to_dict()}
            paths["/v1/invoices"] = {"post": old}
        return {"openapi": "3.1.0", "paths": paths}

    assert openapi_breaking_changes(spec(), spec()) == []
    changed = spec(fields=("id",), required=("number", "note"))
    assert [str(p) for p in openapi_breaking_changes(spec(), changed)] == [
        "POST /v2/invoices.response.number: removed",
        "POST /v2/invoices.request.note: now required",
        "POST /v1/invoices.response.number: removed",
        "POST /v1/invoices.request.note: now required",
    ]
    assert [str(p) for p in openapi_breaking_changes(spec(), spec(extra_path=False))] == [
        "POST /v1/invoices: removed"
    ]
    assert openapi_breaking_changes(spec(deprecated=PAST), spec(extra_path=False)) == []
    assert openapi_breaking_changes(spec(deprecated=FUTURE), spec(extra_path=False))


def test_consumer_contract_checks_the_handled_version():
    from resource_engine_devtools.contracts import consumer_problems

    document = asyncapi_document(registry_with(InvoiceApproved))
    pinned = EventRouter()
    pinned.on("InvoiceApproved", payload=InvoiceApprovedV1, schema_version=1)(lambda e, p: None)
    assert consumer_problems(pinned, document) == []
    every = EventRouter()
    every.on("InvoiceApproved", payload=InvoiceApprovedV1)(lambda e, p: None)
    assert consumer_problems(every, document)  # v2 has no "amount"
