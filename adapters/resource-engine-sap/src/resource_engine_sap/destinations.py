"""SAP BTP Destination service: find a destination by name and call it with
the authentication it is configured with.

```python
from resource_engine_sap import DestinationService, binding, destination_client

destinations = DestinationService(http, binding("destination"))   # VCAP_SERVICES / Kyma
s4 = await destination_client(destinations, "S4HANA")              # base_url + auth
response = await s4.get("/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner")
```

- **Lookup:** ``find_destination`` on the service (subaccount and service
  instance destinations), with the token of the service's own XSUAA client.
  Results are cached until the destination's tokens expire (or
  ``cache_seconds``).
- **Authentication:** the service returns ready-made headers in
  ``authTokens`` (``NoAuthentication``, ``BasicAuthentication``,
  ``OAuth2ClientCredentials``, ``OAuth2SAMLBearerAssertion``,
  ``OAuth2JWTBearer``...). ``destination_client`` sends them, and asks again
  when they expire or the target answers 401. Principal propagation passes
  the caller's token (``user_token=``).
- **On-premise** destinations (``ProxyType: OnPremise``, through the Cloud
  Connector) need the Connectivity service proxy and are refused with a clear
  error.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from resource_engine.errors import ConfigurationError, RemoteCallFailed
from resource_engine_http import OAuth2ClientCredentials
from resource_engine_http.errors import check_response, transport_failed


def binding(service: str, *, name: str | None = None) -> dict[str, Any]:
    """The credentials of a bound BTP service instance.

    Read from ``VCAP_SERVICES`` (Cloud Foundry) or, on Kyma and Kubernetes,
    from ``$SERVICE_BINDING_ROOT/<name>`` (one file per key, as the SAP BTP
    service operator mounts them). ``name`` picks an instance when several
    are bound.
    """
    vcap = os.environ.get("VCAP_SERVICES")
    if vcap:
        for instance in json.loads(vcap).get(service, []):
            if name is None or instance.get("name") == name:
                return dict(instance["credentials"])
    root = os.environ.get("SERVICE_BINDING_ROOT")
    if root:
        for folder in sorted(Path(root).iterdir()):
            kind = folder / "type"
            matches = (kind.exists() and kind.read_text().strip() == service) or folder.name == (
                name or service
            )
            if folder.is_dir() and matches and (name is None or folder.name == name):
                credentials: dict[str, Any] = {}
                for item in folder.iterdir():
                    if item.is_file() and not item.name.startswith("."):
                        text = item.read_text().strip()
                        try:
                            credentials[item.name] = json.loads(text)
                        except ValueError:
                            credentials[item.name] = text
                return credentials
    raise ConfigurationError(
        f"No bound {service!r} service instance",
        hint="Bind the service (cf bind-service, or a ServiceBinding on Kyma), "
        "or pass its credentials yourself.",
    )


@dataclass(frozen=True)
class Destination:
    """A destination found by the service."""

    name: str
    url: str
    authentication: str
    proxy_type: str = "Internet"
    #: Headers to send (from ``authTokens``), e.g. ``{"Authorization": "Bearer ..."}``.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: The whole ``destinationConfiguration`` (additional properties included).
    properties: Mapping[str, Any] = field(default_factory=dict)
    expires_at: float = 0.0


class DestinationService:
    """Client of the Destination service REST API.

    Args:
        client: Caller-owned ``httpx.AsyncClient`` (timeouts, proxies).
        credentials: The service binding: ``uri``, ``url`` (its XSUAA),
            ``clientid``, ``clientsecret``.
        cache_seconds: How long a destination without expiring tokens is kept.
    """

    service = "sap-destination"

    def __init__(
        self,
        client: httpx.AsyncClient,
        credentials: Mapping[str, Any],
        *,
        cache_seconds: float = 300.0,
    ) -> None:
        self.client = client
        self.uri = str(credentials["uri"]).rstrip("/")
        self.auth = OAuth2ClientCredentials(
            str(credentials["url"]).rstrip("/") + "/oauth/token",
            str(credentials["clientid"]),
            str(credentials["clientsecret"]),
        )
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple[str, str | None], Destination] = {}
        self._lock = asyncio.Lock()

    async def find(self, name: str, *, user_token: str | None = None) -> Destination:
        """The destination ``name``, with fresh authentication headers."""
        key = (name, user_token)
        cached = self._cache.get(key)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > time.monotonic():
                return cached
            destination = await self._fetch(name, user_token)
            self._cache[key] = destination
            return destination

    def forget(self, name: str) -> None:
        """Drop ``name`` from the cache (its tokens were refused)."""
        for key in [k for k in self._cache if k[0] == name]:
            del self._cache[key]

    async def _fetch(self, name: str, user_token: str | None) -> Destination:
        headers = {"X-user-token": user_token} if user_token else {}
        url = f"{self.uri}/destination-configuration/v1/destinations/{name}"
        try:
            response = await self.client.get(url, headers=headers, auth=self.auth)
        except httpx.HTTPError as exc:
            raise transport_failed(exc, service=self.service, operation="find") from exc
        if response.status_code == 404:
            raise ConfigurationError(
                f"No destination {name!r} in the subaccount or the service instance",
                hint="Create it in the BTP cockpit (Connectivity > Destinations).",
            )
        check_response(response, service=self.service, operation="find")
        body = response.json()
        config = body.get("destinationConfiguration") or {}
        proxy = config.get("ProxyType", "Internet")
        if proxy != "Internet":
            raise ConfigurationError(
                f"Destination {name!r} is {proxy}: it needs the Connectivity service proxy",
                hint="Reach on-premise systems through the Cloud Connector proxy, "
                "or expose them with an Internet destination.",
            )
        tokens = body.get("authTokens") or []
        failed = [t for t in tokens if t.get("error")]
        if failed:
            raise RemoteCallFailed(
                f"Destination {name!r}: {failed[0]['error']}",
                service=self.service,
                operation="find",
                hint="Check the destination's credentials in the BTP cockpit.",
            )
        auth_headers: dict[str, str] = {}
        lifetimes = []
        for token in tokens:
            header = token.get("http_header") or {}
            if header.get("key"):
                auth_headers[header["key"]] = header["value"]
            elif token.get("value"):
                auth_headers["Authorization"] = f"{token.get('type', 'Bearer')} {token['value']}"
            if token.get("expires_in"):
                lifetimes.append(float(token["expires_in"]))
        lifetime = min([*lifetimes, self.cache_seconds]) if lifetimes else self.cache_seconds
        return Destination(
            name=name,
            url=str(config.get("URL", "")).rstrip("/"),
            authentication=str(config.get("Authentication", "NoAuthentication")),
            proxy_type=proxy,
            headers=auth_headers,
            properties=config,
            expires_at=time.monotonic() + max(lifetime - 30.0, 1.0),
        )


class DestinationAuth(httpx.Auth):
    """``httpx.Auth`` sending a destination's authentication headers, renewed
    when they expire and once more when the target answers 401."""

    requires_response_body = False

    def __init__(
        self, service: DestinationService, name: str, *, user_token: str | None = None
    ) -> None:
        self.service = service
        self.name = name
        self.user_token = user_token

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> Any:  # AsyncGenerator[httpx.Request, httpx.Response]
        destination = await self.service.find(self.name, user_token=self.user_token)
        request.headers.update(destination.headers)
        response = yield request
        if response.status_code == 401:
            self.service.forget(self.name)
            destination = await self.service.find(self.name, user_token=self.user_token)
            request.headers.update(destination.headers)
            yield request

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, Any, None]:
        raise RuntimeError("DestinationAuth works with httpx.AsyncClient only")


async def destination_client(
    service: DestinationService,
    name: str,
    *,
    user_token: str | None = None,
    **httpx_options: Any,
) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` for destination ``name``: its URL as
    ``base_url`` and its authentication. The caller owns (and closes) it."""
    destination = await service.find(name, user_token=user_token)
    httpx_options.setdefault("timeout", 30.0)
    return httpx.AsyncClient(
        base_url=destination.url,
        auth=DestinationAuth(service, name, user_token=user_token),
        **httpx_options,
    )


__all__ = [
    "Destination",
    "DestinationAuth",
    "DestinationService",
    "binding",
    "destination_client",
]
