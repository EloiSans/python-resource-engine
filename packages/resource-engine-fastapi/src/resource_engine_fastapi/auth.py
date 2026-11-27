"""Bearer-token authentication for the generated routes.

Use the returned dependency as ``actor_dependency``::

    mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))

The engine then receives a ``Principal`` as ``actor``; with an authorizer
(``ResourceManager(authorizer=...)``) it decides what the principal may do.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote

from fastapi import Request
from resource_engine.errors import AuthenticationFailed
from resource_engine.identity import authenticate, parse_authorization
from resource_engine.ports.identity import Authenticator, Credentials, Principal

type CertificateSource = Callable[[Request], bytes | None]


def bearer_token(request: Request) -> str | None:
    """``Authorization: Bearer|DPoP <token>``, or an API key in ``X-API-Key``."""
    parsed = parse_authorization(request.headers.get("authorization"))
    if parsed is not None:
        return parsed[1]
    api_key = request.headers.get("x-api-key", "").strip()
    return api_key or None


def request_credentials(
    request: Request,
    *,
    client_certificate: CertificateSource | None = None,
    public_url: str | None = None,
) -> Credentials | None:
    """The request's token, scheme, DPoP proof and client certificate.

    ``public_url`` (``https://api.example.com``) replaces the scheme and host
    the server sees, for the URL a DPoP proof must name behind a proxy.
    """
    parsed = parse_authorization(request.headers.get("authorization"))
    if parsed is None:
        api_key = request.headers.get("x-api-key", "").strip()
        if not api_key:
            return None
        parsed = ("bearer", api_key)
    scheme, token = parsed
    proofs = request.headers.getlist("dpop")
    if len(proofs) > 1:
        raise AuthenticationFailed("Send a single DPoP header", reason="invalid_dpop_proof")
    base = public_url.rstrip("/") if public_url else f"{request.url.scheme}://{request.url.netloc}"
    return Credentials(
        token=token,
        scheme=scheme,
        method=request.method,
        url=base + request.url.path,
        proof=proofs[0] if proofs else None,
        client_certificate=client_certificate(request) if client_certificate else None,
    )


def forwarded_client_certificate(header: str = "x-client-cert") -> CertificateSource:
    """The client certificate a TLS-terminating proxy forwards in ``header``:
    PEM, URL-encoded PEM (nginx ``$ssl_client_escaped_cert``) or base64 DER
    (``X-Forwarded-Client-Cert`` from Envoy needs its ``Cert=`` element).

    Only use it behind a proxy that verifies the certificate and always
    overwrites the header: anyone can send it otherwise.
    """
    name = header.lower()

    def source(request: Request) -> bytes | None:
        value = request.headers.get(name, "").strip()
        if not value:
            return None
        text = unquote(value) if "%" in value else value
        if "-----BEGIN" in text:
            body = text.split("-----BEGIN CERTIFICATE-----", 1)[-1]
            body = body.split("-----END CERTIFICATE-----", 1)[0]
            text = "".join(body.split())
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise AuthenticationFailed(
                "The forwarded client certificate is malformed", reason="invalid_certificate"
            ) from None

    return source


def bearer_actor(
    authenticator: Authenticator,
    *,
    required: bool = True,
    rate_limiter: Any = None,
    client_certificate: CertificateSource | None = None,
    public_url: str | None = None,
) -> Callable[[Request], Awaitable[Principal | None]]:
    """FastAPI dependency: ``Authorization: Bearer <token>`` → ``Principal``.

    Sender-constrained tokens (``Authorization: DPoP`` with a ``DPoP`` proof,
    or certificate-bound tokens) need an authenticator that checks them
    (``resource_engine_oidc.SenderConstrained``); ``client_certificate``
    reads the TLS client certificate (``forwarded_client_certificate()``)
    and ``public_url`` fixes the URL proofs name behind a proxy.

    With ``required=False`` anonymous requests get ``actor=None`` (invalid tokens
    are still rejected). With a ``RateLimiter``, callers get its ``default``
    limit, anonymous clients its ``anonymous`` one by IP, and addresses that
    failed authentication ``failed_auth`` times are refused before their
    credentials are checked (429, RE-E710; see docs/rate-limits.md). The client
    address is ``request.client.host``: behind a proxy, run uvicorn with
    ``--proxy-headers --forwarded-allow-ips`` so it is the real client's.
    """

    async def actor(request: Request) -> Principal | None:
        address = request.client.host if request.client else None
        credentials = request_credentials(
            request, client_certificate=client_certificate, public_url=public_url
        )
        if credentials is None:
            if required:
                raise AuthenticationFailed(
                    "Missing bearer token",
                    reason="missing",
                    hint="Send 'Authorization: Bearer <access token>'.",
                )
            if rate_limiter is not None:
                request.state.rate_limit = await rate_limiter.client(address)
            return None
        if rate_limiter is not None:
            await rate_limiter.before_auth(address)
        try:
            principal = await authenticate(authenticator, credentials)
        except AuthenticationFailed:
            if rate_limiter is not None:
                await rate_limiter.failed(address)
            raise
        request.state.principal = principal  # for the access log and error reports
        if rate_limiter is not None:
            request.state.rate_limit = await rate_limiter.caller(principal)
        return principal

    return actor
