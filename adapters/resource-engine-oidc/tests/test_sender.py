"""Sender-constrained tokens: DPoP proofs (RFC 9449) and certificate-bound
tokens (RFC 8705) around a JWT verifier."""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from resource_engine.errors import AuthenticationFailed
from resource_engine.identity import authenticate
from resource_engine.ports.identity import Credentials
from resource_engine_oidc import (
    OidcAuthenticator,
    SenderConstrained,
    access_token_hash,
    certificate_thumbprint,
    jwk_thumbprint,
)
from resource_engine_oidc.sender import normalise_url

ISS = "https://sso.example.com"
URL = "https://api.example.com/invoices"


class Client:
    """A client's DPoP key and the proofs it signs."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.key.public_key()))
        self.jkt = jwk_thumbprint(self.jwk)

    def proof(self, token: str, *, method="GET", url=URL, iat=None, jti=None, **extra) -> str:
        claims = {
            "htm": method,
            "htu": url,
            "iat": int(time.time()) if iat is None else iat,
            "jti": jti or uuid.uuid4().hex,
            "ath": access_token_hash(token),
            **extra,
        }
        headers = {"typ": "dpop+jwt", "jwk": self.jwk}
        return jwt.encode(claims, self.key, algorithm="ES256", headers=headers)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def auth(http):
    return SenderConstrained(
        OidcAuthenticator(issuer=ISS, audience="api", jwks_uri=f"{ISS}/jwks", http=http),
        mtls=True,
    )


def _token(signer, **claims) -> str:
    return signer.token({"iss": ISS, "aud": "api", "sub": "ada", **claims})


async def _reason(auth, credentials) -> str:
    with pytest.raises(AuthenticationFailed) as info:
        await authenticate(auth, credentials)
    return info.value.reason


def _dpop(token, proof, *, method="GET", url=URL) -> Credentials:
    return Credentials(token=token, scheme="dpop", method=method, url=url, proof=proof)


async def test_a_dpop_bound_token_needs_a_proof_by_its_key(signer, client, auth):
    token = _token(signer, cnf={"jkt": client.jkt})
    principal = await authenticate(auth, _dpop(token, client.proof(token)))
    assert principal.subject == "ada"

    # stolen: the same token sent as a bearer token, without a proof, or by another key
    assert await _reason(auth, Credentials(token=token, method="GET", url=URL)) == "invalid_token"
    assert await _reason(auth, _dpop(token, None)) == "invalid_dpop_proof"
    thief = Client()
    assert await _reason(auth, _dpop(token, thief.proof(token))) == "invalid_dpop_proof"


@pytest.mark.parametrize(
    "change",
    [
        {"method": "POST"},
        {"url": "https://api.example.com/customers"},
        {"url": "https://evil.example.com/invoices"},
        {"iat": int(time.time()) - 600},
        {"iat": int(time.time()) + 600},
        {"ath": "not-the-token-hash"},
    ],
)
async def test_proofs_name_this_request_and_this_token(signer, client, auth, change):
    token = _token(signer, cnf={"jkt": client.jkt})
    assert await _reason(auth, _dpop(token, client.proof(token, **change))) == "invalid_dpop_proof"


async def test_a_proof_is_used_once(signer, client, auth):
    token = _token(signer, cnf={"jkt": client.jkt})
    proof = client.proof(token)
    await authenticate(auth, _dpop(token, proof))
    assert await _reason(auth, _dpop(token, proof)) == "invalid_dpop_proof"


async def test_malformed_proofs(signer, client, auth):
    token = _token(signer, cnf={"jkt": client.jkt})
    good = client.proof(token)
    header = jwt.get_unverified_header(good)
    claims = jwt.decode(good, options={"verify_signature": False})
    wrong_typ = jwt.encode(claims, client.key, algorithm="ES256", headers={**header, "typ": "JWT"})
    private = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(client.key))
    with_private = jwt.encode(
        claims, client.key, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": private}
    )
    hmac_proof = jwt.encode(claims, "secret" * 6, algorithm="HS256", headers={"typ": "dpop+jwt"})
    for proof in ("not-a-jwt", wrong_typ, with_private, hmac_proof, good[:-4] + "AAAA"):
        assert await _reason(auth, _dpop(token, proof)) == "invalid_dpop_proof"


async def test_the_dpop_scheme_with_an_unbound_token_is_refused(signer, client, auth):
    token = _token(signer)
    assert await _reason(auth, _dpop(token, client.proof(token))) == "invalid_token"
    # the same token as a bearer token is fine unless binding is required
    assert (await authenticate(auth, Credentials(token=token))).subject == "ada"
    strict = SenderConstrained(auth.authenticator, required=True)
    assert await _reason(strict, Credentials(token=token)) == "invalid_token"


async def test_certificate_bound_tokens(signer, auth):
    certificate = b"0\x82 a DER certificate"
    token = _token(signer, cnf={"x5t#S256": certificate_thumbprint(certificate)})
    ok = await authenticate(auth, Credentials(token=token, client_certificate=certificate))
    assert ok.subject == "ada"
    assert await _reason(auth, Credentials(token=token)) == "invalid_token"
    other = Credentials(token=token, client_certificate=b"another certificate")
    assert await _reason(auth, other) == "invalid_token"
    dpop_only = SenderConstrained(auth.authenticator)
    mine = Credentials(token=token, client_certificate=certificate)
    assert await _reason(dpop_only, mine) == "invalid_token"


async def test_plain_authenticators_refuse_the_dpop_scheme(signer, client, http):
    plain = OidcAuthenticator(issuer=ISS, audience="api", jwks_uri=f"{ISS}/jwks", http=http)
    token = _token(signer, cnf={"jkt": client.jkt})
    assert await _reason(plain, _dpop(token, client.proof(token))) == "unsupported_scheme"


async def test_a_shared_replay_cache_without_add_still_refuses_replays(signer, client, http):
    class PlainCache:
        def __init__(self):
            self.items = {}

        async def get(self, key):
            return self.items.get(key)

        async def set(self, key, value, ttl):
            self.items[key] = value

        async def delete(self, *keys):
            pass

    verifier = OidcAuthenticator(issuer=ISS, audience="api", jwks_uri=f"{ISS}/jwks", http=http)
    auth = SenderConstrained(verifier, replay_cache=PlainCache())
    token = _token(signer, cnf={"jkt": client.jkt})
    proof = client.proof(token)
    await authenticate(auth, _dpop(token, proof))
    assert await _reason(auth, _dpop(token, proof)) == "invalid_dpop_proof"


def test_thumbprints_and_urls():
    # RFC 7638 §3.1 example
    rsa_jwk = {
        "kty": "RSA",
        "n": (
            "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1"
            "L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4"
            "QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbO"
            "pbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csF"
            "Cur-kEgU8awapJzKnqDKgw"
        ),
        "e": "AQAB",
        "alg": "RS256",
        "kid": "2011-04-29",
    }
    assert jwk_thumbprint(rsa_jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"
    assert normalise_url("HTTPS://API.example.com:443/a?b=1#c") == "https://api.example.com/a"
    assert normalise_url("http://localhost:8000/x") == "http://localhost:8000/x"
    with pytest.raises(ValueError):
        SenderConstrained(object(), dpop=False)  # type: ignore[arg-type]
