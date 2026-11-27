"""Authenticating a request: a token, and for sender-constrained tokens the
proof that the caller holds the key the token is bound to.

Entry adapters build ``Credentials`` from the request and call
``authenticate``. Plain ``Authenticator`` implementations see the token only;
a ``RequestAuthenticator`` (``resource_engine_oidc.SenderConstrained``) also
checks the DPoP proof or the client certificate. See docs/identity.md.
"""

from __future__ import annotations

from resource_engine.errors import AuthenticationFailed
from resource_engine.ports.identity import Authenticator, Credentials, Principal

SCHEMES = ("bearer", "dpop")


def parse_authorization(header: str | None) -> tuple[str, str] | None:
    """``Authorization`` → ``(scheme, token)`` for ``Bearer`` and ``DPoP``."""
    scheme, _, token = (header or "").partition(" ")
    scheme, token = scheme.lower(), token.strip()
    if scheme in SCHEMES and token:
        return scheme, token
    return None


async def authenticate(authenticator: Authenticator, credentials: Credentials) -> Principal:
    """The caller behind ``credentials``.

    A ``DPoP`` token needs an authenticator that checks proofs: with any other
    it is refused rather than accepted as a bearer token.
    """
    check = getattr(authenticator, "authenticate_request", None)
    if check is not None:
        return await check(credentials)
    if credentials.scheme != "bearer":
        raise AuthenticationFailed(
            "DPoP tokens are not accepted here",
            reason="unsupported_scheme",
            hint="Wrap the authenticator in SenderConstrained to accept DPoP.",
        )
    return await authenticator.authenticate(credentials.token)


__all__ = ["SCHEMES", "authenticate", "parse_authorization"]
