# Identity (authentication)

The engine needs to know **who** calls before it can decide **what** they may do.
This page covers the first part; [permissions](permissions.md) the second.

## Principal

Entry adapters turn credentials into a `resource_engine.ports.identity.Principal`
and pass it as `actor`. Hooks, actions and documents see it in `ctx.actor`.

| Field | Content |
|---|---|
| `subject`, `issuer`, `provider` | Who, vouched for by whom; `id` = `<provider>:<subject>` |
| `name`, `email` | Display data, if present |
| `roles`, `groups`, `scopes` | Normalised across providers (see below) |
| `tenant`, `client_id`, `service` | Tenant, OAuth client, machine identity |
| `claims` | Every verified claim |

## Verifying tokens (`resource-engine-oidc`)

```python
from resource_engine_fastapi import bearer_actor
from resource_engine_oidc import keycloak

authenticator = keycloak("https://sso.example.com", "acme", audience="invoices-api")
mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))
```

`OidcAuthenticator` checks signature (JWKS, cached; refreshed on key rotation),
`iss`, `aud`, `exp`/`nbf`/`iat` with 30 s leeway, and refuses `none` and HMAC
algorithms. Failures raise `AuthenticationFailed` (RE-E701, HTTP 401 with
`WWW-Authenticate: Bearer error="invalid_token"`) with a `reason`; tokens are never
echoed.

| Preset | Issuer | Roles | Notes | Tested against |
|---|---|---|---|---|
| `keycloak(base, realm, audience=, tenant_claim=)` | `<base>/realms/<realm>` | realm roles + client roles (built-ins removed) | add an *audience* mapper to the client; `tenant_claim="organization"` takes the tenant from Keycloak Organizations (one organization per user), or name any claim | real Keycloak (compose), the [example app](example-app.md) |
| `cognito(region, pool, client_ids=)` | `cognito-idp.<region>.amazonaws.com/<pool>` | `cognito:groups` | access tokens: checks `client_id` and `token_use`; `token_use="id"` for ID tokens | signed test tokens |
| `entra_id(tenant, audience=)` | `login.microsoftonline.com/<tid>/v2.0` | app `roles`; `groups`; `scp` → scopes | v2 tokens; `organizations` + `allowed_tenants` for multi-tenant; subject = `oid` | signed test tokens |
| `google_identity_platform(project)` | `securetoken.google.com/<project>` | custom claim (`roles_claim`) | Firebase Auth / Identity Platform; `tenants=` for multi-tenancy | signed test tokens |
| `google_id_token(audience)` | `accounts.google.com` | – | Cloud Run / Pub/Sub push OIDC; `service_accounts=` allow-list | signed test tokens |

Other OIDC providers (Auth0, Okta, Zitadel, Authentik…) work with
`OidcAuthenticator(issuer=..., audience=...)` and discovery; pass `to_principal`
to map their role claims.

## Opaque tokens (introspection)

Some providers or clients issue opaque access tokens, which are random
strings, not JWTs. `IntrospectionAuthenticator` asks the provider about each
one (OAuth 2.0 token introspection, RFC 7662) with the API's own
confidential client:

```python
from resource_engine_oidc import keycloak_introspection, okta_introspection

tokens = keycloak_introspection(
    "https://sso.example.com",
    "acme",
    client_id="invoices-api",
    client_secret=SECRET,
    audience="invoices-api",
    tenant_claim="organization",
)
tokens = okta_introspection(
    "acme.okta.com", client_id="invoices-api", client_secret=SECRET, audience="api://invoices"
)
mount_resources(app, registry, manager, actor_dependency=bearer_actor(tokens))
```

- **Checks:** `active`, then `exp`/`nbf` (30 s leeway), `iss` and `aud` when
  configured. An inactive token (expired, revoked, logged out, unknown) is
  RE-E701 with reason `inactive`. A provider that can't be reached, or that
  refuses the API's credentials, is RE-E501 (502), not a 401.
- **Cache:** active answers are cached by token hash for `cache_ttl` seconds
  (30 by default), never past `exp`. That's how long a revoked token can
  still work. `cache_ttl=0` asks every time. Pass `cache=RedisCache(...)` to
  share answers between instances. The default is an in-memory cache.
- **Immediate revocation for JWTs:** introspection also works for Keycloak's
  JWTs, so a logout or a disabled user takes effect within `cache_ttl` instead
  of at the token's expiry. It costs one call per token and cache period.
- **Both kinds:** `TokenRouter(jwt=keycloak(...), opaque=keycloak_introspection(...))`
  checks JWTs locally and introspects everything else.
- **Mapping:** Keycloak claims map like the `keycloak` preset (realm and
  client roles, tenant). Others use `sub` (or `client_id` for client
  credentials, then `service=True`), `scope`, `groups` and `roles`. Pass
  `to_principal` for anything else.

Auth0 has no introspection endpoint: its API access tokens are JWTs, so use
`OidcAuthenticator` with its issuer and API identifier as audience. Entra ID
and Cognito issue JWTs too.

| Preset | Endpoint | Tested against |
|---|---|---|
| `keycloak_introspection(base, realm, client_id=, client_secret=)` | `<base>/realms/<realm>/protocol/openid-connect/token/introspect` | real Keycloak (compose), revocation after logout |
| `okta_introspection(domain, client_id=, client_secret=, authorization_server="default")` | `https://<domain>/oauth2/<server>/v1/introspect` | fake endpoint |
| `IntrospectionAuthenticator(endpoint, client_id=, client_secret=)` | any RFC 7662 endpoint | fake endpoint |

## API keys

Machine clients that can't use OAuth get API keys: `registry.register_api_keys()`
and `ApiKeyAuthenticator(uow_factory, fallback=oidc)` in front of the OIDC
verifier. See [API keys](api-keys.md).

## Outbound: calling other services

`resource_engine_http.OAuth2ClientCredentials` gets and caches client-credentials
tokens for `httpx` clients (SAP BTP, Entra ID, Keycloak, Cognito…). It is tested
against the Keycloak realm in `docker/keycloak/`.

## Local Keycloak

`docker compose -f compose.emulators.yaml up -d keycloak` starts Keycloak on
http://127.0.0.1:8180 (admin / admin) with the `resource-engine` realm: a
confidential client `resource-engine-service` (secret `resource-engine-dev-secret`,
dev only) whose tokens carry the audience `resource-engine-api` and the role
`accountant`.
