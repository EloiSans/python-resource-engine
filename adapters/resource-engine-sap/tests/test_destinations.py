"""BTP Destination service: bindings, lookup, cached tokens, renewal on 401,
principal propagation and refused on-premise destinations."""

from __future__ import annotations

import json

import httpx
import pytest
from resource_engine.errors import ConfigurationError, RemoteCallFailed
from resource_engine_sap import DestinationService, binding, destination_client

CREDENTIALS = {
    "uri": "https://destination-configuration.cfapps.eu10.hana.ondemand.com",
    "url": "https://acme.authentication.eu10.hana.ondemand.com",
    "clientid": "sb-clone",
    "clientsecret": "secret",
}


class FakeBtp:
    def __init__(self) -> None:
        self.lookups: list[httpx.Request] = []
        self.calls: list[httpx.Request] = []
        self.sent: list[str] = []  # Authorization of each call (requests are reused on retry)
        self.issued = 0
        self.proxy = "Internet"

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "service-token", "expires_in": 3600})
        if request.url.path.startswith("/destination-configuration/v1/destinations/"):
            self.lookups.append(request)
            assert request.headers["authorization"] == "Bearer service-token"
            name = request.url.path.rsplit("/", 1)[-1]
            if name == "missing":
                return httpx.Response(404, json={"ErrorMessage": "not found"})
            self.issued += 1
            token = {
                "type": "Bearer",
                "value": f"t{self.issued}",
                "expires_in": "3600",
                "http_header": {"key": "Authorization", "value": f"Bearer t{self.issued}"},
            }
            if name == "broken":
                token = {"type": "", "value": "", "error": "Retrieval of OAuth token failed"}
            return httpx.Response(
                200,
                json={
                    "destinationConfiguration": {
                        "Name": name,
                        "URL": "https://s4.example.com/",
                        "ProxyType": self.proxy,
                        "Authentication": "OAuth2ClientCredentials",
                        "sap-client": "100",
                    },
                    "authTokens": [token],
                },
            )
        self.calls.append(request)
        self.sent.append(request.headers.get("authorization"))
        if request.headers.get("authorization") == "Bearer expired":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})


@pytest.fixture
def btp():
    fake = FakeBtp()
    transport = httpx.MockTransport(fake.handler)
    return fake, transport, DestinationService(httpx.AsyncClient(transport=transport), CREDENTIALS)


async def test_lookup_is_cached_and_used_by_the_client(btp):
    fake, transport, service = btp
    destination = await service.find("S4HANA")
    assert destination.url == "https://s4.example.com"
    assert destination.headers == {"Authorization": "Bearer t1"}
    assert destination.properties["sap-client"] == "100"
    client = await destination_client(service, "S4HANA", transport=transport)
    response = await client.get("/sap/opu/odata/sap/API/Items")
    assert response.json() == {"ok": True}
    assert str(fake.calls[0].url) == "https://s4.example.com/sap/opu/odata/sap/API/Items"
    assert fake.calls[0].headers["authorization"] == "Bearer t1"
    assert len(fake.lookups) == 1  # cached


async def test_a_refused_token_is_renewed_once(btp):
    fake, transport, service = btp
    client = await destination_client(service, "S4HANA", transport=transport)
    service._cache[("S4HANA", None)] = service._cache[("S4HANA", None)].__class__(
        **{
            **service._cache[("S4HANA", None)].__dict__,
            "headers": {"Authorization": "Bearer expired"},
        }
    )
    response = await client.get("/x")
    assert response.status_code == 200
    assert fake.sent == ["Bearer expired", "Bearer t2"]


async def test_user_token_is_propagated_and_cached_apart(btp):
    fake, _, service = btp
    await service.find("S4HANA", user_token="jwt-of-alice")
    await service.find("S4HANA")
    assert fake.lookups[0].headers["x-user-token"] == "jwt-of-alice"
    assert "x-user-token" not in fake.lookups[1].headers


async def test_missing_broken_and_on_premise(btp):
    fake, _, service = btp
    with pytest.raises(ConfigurationError, match="No destination 'missing'"):
        await service.find("missing")
    with pytest.raises(RemoteCallFailed, match="OAuth token failed"):
        await service.find("broken")
    fake.proxy = "OnPremise"
    with pytest.raises(ConfigurationError, match="Connectivity"):
        await service.find("ERP")


def test_bindings_from_cloud_foundry_and_kyma(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "VCAP_SERVICES",
        json.dumps({"destination": [{"name": "dest", "credentials": CREDENTIALS}]}),
    )
    assert binding("destination") == CREDENTIALS
    monkeypatch.delenv("VCAP_SERVICES")
    folder = tmp_path / "my-destination"
    folder.mkdir()
    (folder / "type").write_text("destination")
    for key, value in CREDENTIALS.items():
        (folder / key).write_text(value)
    monkeypatch.setenv("SERVICE_BINDING_ROOT", str(tmp_path))
    assert binding("destination") == {**CREDENTIALS, "type": "destination"}
    with pytest.raises(ConfigurationError):
        binding("connectivity")
