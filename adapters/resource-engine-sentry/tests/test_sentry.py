"""SentryReporter: engine failures become Sentry (GlitchTip) events with context."""

from __future__ import annotations

import logging

import httpx
import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from resource_engine import reporting
from resource_engine.errors import RemoteCallFailed, ValidationFailed
from resource_engine.logs import bind_log_context
from resource_engine.ports.identity import Principal
from resource_engine_fastapi import install_access_log, install_exception_handlers
from resource_engine_sentry import SentryReporter
from sentry_sdk.transport import Transport


class Capture(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def sentry():
    transport = Capture()
    reporter = SentryReporter(
        dsn="https://key@glitchtip.example.com/1",
        environment="test",
        transport=transport,
        default_integrations=False,
        tags={"service": "billing"},
    )
    reporting.set_reporter(reporter)
    yield transport
    reporting.set_reporter()
    sentry_sdk.get_client().close()


def _event(transport) -> dict:
    sentry_sdk.flush()
    assert len(transport.events) == 1, transport.events
    return transport.events[0]


async def test_a_failure_becomes_one_event_with_context(sentry):
    error = RemoteCallFailed(
        "billing-api get returned HTTP 503", service="billing-api", operation="get", status=503
    )
    with bind_log_context(resource="invoices", operation="get", correlation_id="c-1"):
        await reporting.report_error(
            error, where="operation", actor=Principal(subject="ada", issuer="i", provider="kc")
        )
        await reporting.report_error(error, where="http")  # the same failure: not again
    event = _event(sentry)
    assert event["level"] == "error" and event["environment"] == "test"
    tags = event["tags"]
    assert tags["resource_engine.code"] == "RE-E501"
    assert tags["resource_engine.where"] == "operation"
    assert tags["resource_engine.resource"] == "invoices"
    assert tags["service"] == "billing"
    assert event["contexts"]["resource_engine"]["correlation_id"] == "c-1"
    assert event["user"] == {"id": "kc:ada"}
    assert event["fingerprint"] == ["RE-E501", "operation", "invoices"]
    assert event["exception"]["values"][0]["type"] == "RemoteCallFailed"


async def test_expected_errors_are_not_sent(sentry):
    await reporting.report_error(ValidationFailed("bad input"), where="operation")
    sentry_sdk.flush()
    assert sentry.events == []


async def test_dead_letters_of_expected_errors_are_warnings(sentry):
    await reporting.report_error(ValidationFailed("bad payload"), where="consumer", force=True)
    assert _event(sentry)["level"] == "warning"


def test_http_500s_are_reported_once_with_the_route(sentry, caplog):
    app = FastAPI()
    install_exception_handlers(app)
    install_access_log(app)

    @app.get("/invoices/{invoice_id}")
    async def broken(invoice_id: int):
        raise RuntimeError("boom")

    @app.get("/upstream")
    async def upstream():
        raise RemoteCallFailed("down", service="billing-api", operation="get")

    with (
        caplog.at_level(logging.INFO, logger="resource_engine.access"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        r = client.get("/invoices/7", headers={"X-Request-Id": "req-42"})
        assert r.status_code == 500
        assert r.json() == {"error": {"code": "RE-E000", "message": "Internal error"}}
        assert r.headers["x-request-id"] == "req-42"
        assert client.get("/upstream").status_code == 502
    sentry_sdk.flush()
    first, second = sentry.events
    assert first["tags"]["resource_engine.route"] == "/invoices/{invoice_id}"  # no ids
    assert first["contexts"]["resource_engine"]["request_id"] == "req-42"
    assert second["tags"]["resource_engine.code"] == "RE-E501"
    access = [r for r in caplog.records if r.name == "resource_engine.access"]
    assert [(r.http_route, r.http_status) for r in access] == [
        ("/invoices/{invoice_id}", 500),
        ("/upstream", 502),
    ]


async def test_outgoing_calls_are_logged(caplog):
    from resource_engine_http import RestEndpoint, RestRepository

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503 if "fail" in request.url.path else 200, json={"id": 1})

    client = httpx.AsyncClient(
        base_url="https://api.example.com", transport=httpx.MockTransport(handler)
    )
    repo = RestRepository(
        client, RestEndpoint(path="/items", to_entity=dict, to_payload=dict, service="items-api")
    )
    with caplog.at_level(logging.INFO, logger="resource_engine.outgoing"):
        await repo.get(1)
        repo.endpoint = RestEndpoint(
            path="/fail", to_entity=dict, to_payload=dict, service="items-api"
        )
        with pytest.raises(RemoteCallFailed):
            await repo.get(1)
    lines = [r for r in caplog.records if r.name == "resource_engine.outgoing"]
    assert [(r.remote_service, r.http_status, r.outcome, r.levelname) for r in lines] == [
        ("items-api", 200, "ok", "INFO"),
        ("items-api", 503, "failed", "WARNING"),
    ]
    assert lines[0].url == "https://api.example.com/items/1"
