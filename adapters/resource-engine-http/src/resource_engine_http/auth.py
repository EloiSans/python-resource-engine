"""Outbound authentication for HTTP adapters.

This is how the engine authenticates *to* external services. Inbound
authorisation of engine operations (gates and policies) is a separate, planned
feature.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

import httpx


class OAuth2ClientCredentials(httpx.Auth):
    """OAuth 2.0 client credentials grant as an ``httpx.Auth``.

    The token is cached until shortly before it expires and refreshed once when
    the service answers 401. Works with SAP BTP (XSUAA), Entra ID, Keycloak, etc.

    Args:
        token_url: Token endpoint (e.g. ``https://<subdomain>.authentication.<region>.hana.ondemand.com/oauth/token``).
        client_id: Client id.
        client_secret: Client secret; sent with HTTP Basic auth.
        scope: Optional space-separated scopes.
        extra: Extra form fields for the token request (e.g. ``{"resource": ...}``).
        leeway: Seconds before ``expires_in`` at which the token is renewed.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        scope: str | None = None,
        extra: dict[str, str] | None = None,
        leeway: float = 30.0,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.extra = extra or {}
        self.leeway = leeway
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _token_request(self) -> httpx.Request:
        form = {"grant_type": "client_credentials", **self.extra}
        if self.scope:
            form["scope"] = self.scope
        request = httpx.Request("POST", self.token_url, data=form)
        return next(httpx.BasicAuth(self.client_id, self.client_secret).auth_flow(request))

    def _store(self, response: httpx.Response) -> None:
        if not response.is_success:
            raise httpx.HTTPStatusError(
                f"Token request failed with HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
        body = response.json()
        self._token = body["access_token"]
        self._expires_at = time.monotonic() + float(body.get("expires_in", 300)) - self.leeway

    def _valid(self) -> bool:
        return self._token is not None and time.monotonic() < self._expires_at

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        async with self._lock:
            if not self._valid():
                token_response = yield self._token_request()
                await token_response.aread()
                self._store(token_response)
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            async with self._lock:
                token_response = yield self._token_request()
                await token_response.aread()
                self._store(token_response)
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request

    def sync_auth_flow(self, request):  # pragma: no cover - adapters are async only
        raise RuntimeError("OAuth2ClientCredentials supports httpx.AsyncClient only")
