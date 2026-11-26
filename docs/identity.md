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
