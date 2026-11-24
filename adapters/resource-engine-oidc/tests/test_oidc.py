from __future__ import annotations

import time

import httpx
import jwt
import pytest
from resource_engine.errors import AuthenticationFailed
from resource_engine_oidc import (
    cognito,
    entra_id,
    google_id_token,
    google_identity_platform,
    keycloak,
)

KC_ISS = "https://sso.example.com/realms/acme"
TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-2222-3333-4444-555555555555"


async def reason(auth, token) -> str:
    with pytest.raises(AuthenticationFailed) as info:
        await auth.authenticate(token)
    return info.value.reason


async def test_keycloak_roles_and_service_account(signer, http):
    auth = keycloak("https://sso.example.com", "acme", audience="invoices-api", http=http)
    token = signer.token(
        {
            "iss": KC_ISS,
            "aud": ["invoices-api", "account"],
            "sub": "u-1",
            "azp": "invoices-web",
            "preferred_username": "anna",
            "email": "anna@example.com",
            "scope": "openid email",
            "realm_access": {"roles": ["accountant", "offline_access", "default-roles-acme"]},
            "resource_access": {"invoices-web": {"roles": ["approver"]}},
        }
    )
    p = await auth.authenticate(token)
    assert p.roles == {"accountant", "approver"} and p.scopes == {"openid", "email"}
    assert (p.provider, p.subject, p.name, p.service) == ("keycloak", "u-1", "anna", False)
    assert p.id == "keycloak:u-1"


async def test_rejections(signer, http):
    auth = keycloak("https://sso.example.com", "acme", audience="invoices-api", http=http)
    base = {"iss": KC_ISS, "aud": "invoices-api", "sub": "u"}
    assert await reason(auth, signer.token(base, ttl=-120)) == "expired"
    assert await reason(auth, signer.token({**base, "aud": "other"})) == "invalid_audience"
    assert await reason(auth, signer.token({**base, "iss": "https://evil"})) == "invalid_issuer"
    assert await reason(auth, "not-a-jwt") == "invalid_token"
    hs = jwt.encode({**base, "exp": int(time.time()) + 60, "iat": 0}, "secret" * 8, "HS256")
    assert await reason(auth, hs) == "invalid_algorithm"
    forged = signer.token(base)
    signer.rotate()  # same kid header, different key: signature check fails
    forged_kid = jwt.get_unverified_header(forged)["kid"]
    signer.keys[forged_kid] = signer.keys[signer.kid]
    auth.jwks._fetched -= auth.jwks.ttl + 1  # stale: force a refresh (relative to now)
    assert await reason(auth, forged) == "invalid_signature"


async def test_key_rotation_refreshes_jwks_once(signer, http):
    auth = keycloak("https://sso.example.com", "acme", audience="api", http=http)
    claims = {"iss": KC_ISS, "aud": "api", "sub": "u"}
    await auth.authenticate(signer.token(claims))
    await auth.authenticate(signer.token(claims))
    assert signer.jwks_calls == 1  # cached
    signer.rotate()
    auth.jwks._fetched -= 60  # past the min refresh interval
    await auth.authenticate(signer.token(claims))
    assert signer.jwks_calls == 2


async def test_unknown_kid(signer, http):
    auth = keycloak("https://sso.example.com", "acme", audience="api", http=http)
    token = jwt.encode(
        {"iss": KC_ISS, "aud": "api", "sub": "u", "exp": int(time.time()) + 60, "iat": 0},
        signer.keys[signer.kid],
        algorithm="RS256",
        headers={"kid": "unknown"},
    )
    assert await reason(auth, token) == "unknown_key"


async def test_cognito_access_and_id_tokens(signer, http):
    iss = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_Abc"
    access = cognito("eu-west-1", "eu-west-1_Abc", client_ids="app1", http=http)
    token = signer.token(
        {
            "iss": iss,
            "sub": "s-1",
            "client_id": "app1",
            "token_use": "access",
            "username": "joan",
            "cognito:groups": ["admins"],
            "scope": "aws.cognito.signin.user.admin",
        }
    )
    p = await access.authenticate(token)
    assert p.roles == {"admins"} and p.name == "joan" and not p.service
    machine = signer.token(
        {"iss": iss, "sub": "app1", "client_id": "app1", "token_use": "access", "scope": "api/r"}
    )
    assert (await access.authenticate(machine)).service
    wrong_client = signer.token({"iss": iss, "sub": "s", "client_id": "x", "token_use": "access"})
    assert await reason(access, wrong_client) == "invalid_audience"
    id_token = signer.token({"iss": iss, "sub": "s", "aud": "app1", "token_use": "id"})
    assert await reason(access, id_token) == "invalid_token"  # an ID token is not an access token
    ids = cognito("eu-west-1", "eu-west-1_Abc", client_ids=["app1"], token_use="id", http=http)
    assert (await ids.authenticate(id_token)).subject == "s"


async def test_entra_single_and_multi_tenant(signer, http):
    def token(issuer_tid: str, **extra):
        return signer.token(
            {
                "iss": f"https://login.microsoftonline.com/{issuer_tid}/v2.0",
                "aud": "api://invoices",
                "sub": "pairwise",
                "oid": "object-id",
                "tid": issuer_tid,
                "scp": "Invoices.Read Invoices.Write",
                "roles": ["Invoices.Approver"],
                "groups": ["g1"],
                "preferred_username": "anna@contoso.com",
                **extra,
            }
        )

    single = entra_id(TENANT, audience="api://invoices", http=http)
    p = await single.authenticate(token(TENANT))
    assert p.subject == "object-id" and p.tenant == TENANT
    assert p.scopes == {"Invoices.Read", "Invoices.Write"}
    assert p.roles == {"Invoices.Approver"} and p.groups == {"g1"} and not p.service
    assert await reason(single, token(OTHER_TENANT)) == "invalid_issuer"
    mismatch = token(TENANT, tid=OTHER_TENANT)
    assert await reason(single, mismatch) == "invalid_issuer"

    multi = entra_id(
        "organizations", audience="api://invoices", allowed_tenants=[TENANT], http=http
    )
    assert (await multi.authenticate(token(TENANT))).tenant == TENANT
    assert await reason(multi, token(OTHER_TENANT)) == "invalid_issuer"
    app_only = token(TENANT, scp=None, idtyp="app")
    assert (await single.authenticate(app_only)).service


async def test_google_identity_platform(signer, http):
    auth = google_identity_platform("acme-prod", tenants=["t-1"], http=http)
    claims = {
        "iss": "https://securetoken.google.com/acme-prod",
        "aud": "acme-prod",
        "sub": "uid-1",
        "email": "x@acme.com",
        "roles": ["clerk"],
        "firebase": {"tenant": "t-1", "sign_in_provider": "password"},
    }
    p = await auth.authenticate(signer.token(claims))
    assert p.roles == {"clerk"} and p.tenant == "t-1"
    other = {**claims, "firebase": {"tenant": "t-2"}}
    assert await reason(auth, signer.token(other)) == "invalid_tenant"
    assert await reason(auth, signer.token({**claims, "aud": "other"})) == "invalid_audience"


async def test_google_id_token_for_service_calls(signer, http):
    sa = "pusher@acme.iam.gserviceaccount.com"
    auth = google_id_token("https://invoices.run.app", service_accounts=[sa], http=http)
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "https://invoices.run.app",
        "sub": "1",
        "email": sa,
        "email_verified": True,
    }
    assert (await auth.authenticate(signer.token(claims))).service
    stranger = {**claims, "email": "other@acme.iam.gserviceaccount.com"}
    assert await reason(auth, signer.token(stranger)) == "forbidden_caller"


@pytest.mark.emulator("keycloak")
async def test_real_keycloak_service_account_token():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8180/realms/resource-engine/protocol/openid-connect/token",
            data={"grant_type": "client_credentials"},
            auth=("resource-engine-service", "resource-engine-dev-secret"),
        )
        token = response.json()["access_token"]
        auth = keycloak(
            "http://127.0.0.1:8180", "resource-engine", audience="resource-engine-api", http=client
        )
        principal = await auth.authenticate(token)
    assert principal.roles == {"accountant"} and principal.service
    assert principal.client_id == "resource-engine-service"


@pytest.mark.emulator("keycloak")
async def test_keycloak_token_to_permissions_end_to_end():
    """Real token → Principal → role mapping → system role → engine decision."""
    from invoices.bootstrap import authz, registry
    from resource_engine import ResourceManager
    from resource_engine.authz import PolicyAuthorizer
    from resource_engine.errors import PermissionDenied
    from resource_engine.testing import InMemoryBlobStore, InMemoryStore

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8180/realms/resource-engine/protocol/openid-connect/token",
            data={"grant_type": "client_credentials"},
            auth=("resource-engine-service", "resource-engine-dev-secret"),
        )
        auth = keycloak(
            "http://127.0.0.1:8180", "resource-engine", audience="resource-engine-api", http=client
        )
        principal = await auth.authenticate(response.json()["access_token"])

    store = InMemoryStore.from_registry(registry)
    manager = ResourceManager(
        registry,
        store.unit_of_work,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        blob_store=InMemoryBlobStore(),
    )
    # the realm gives the service account "accountant", mapped to the viewer role
    assert (await manager.list("invoices", actor=principal)).total == 0
    with pytest.raises(PermissionDenied):
        await manager.create("invoices", {"customer": "ACME", "amount": "1"}, actor=principal)
