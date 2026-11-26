# API keys

API keys are for machine clients that can't use OAuth: a partner's server, a
script, a cron job on a box without a client secret flow. Prefer OAuth client
credentials when the client can do it. Keys are long-lived bearer secrets,
and this page covers how the engine limits the damage.

```python
registry.register_authz()
registry.register_api_keys()  # the authz_api_keys resource

keys = ApiKeyAuthenticator(manager.uow_factory, fallback=oidc)  # rek_ keys here, the rest to OIDC
mount_resources(app, registry, manager, actor_dependency=bearer_actor(keys))
mount_graphql(app, registry, manager, actor_dependency=bearer_actor(keys), authenticator=keys)
```

SQL: `engine_tables(metadata, api_keys=True, ...)` declares
`resource_engine_api_keys`, and `map_engine` maps it.

## Creating a key

```
POST /authz_api_keys
{"name": "ERP sync", "roles": ["viewer"], "expires_at": "2027-06-30T00:00:00Z"}
```

```json
{"id": "9f2c41d07ab35e10", "name": "ERP sync", "roles": ["viewer"], "tenant": "acme",
 "status": "active", "expires_at": "2027-06-30T00:00:00Z", "created_by": "keycloak:5f2…",
 "key": "rek_9f2c41d07ab35e10_x3Jw…"}
```

- **Shown once.** `key` is only in the create response. Later reads, and
  replays of the same idempotency key, return `null`. The engine stores a
  SHA-256 hash of the secret, never the secret: the secret is 256 random bits,
  so a fast hash is enough.
- **No escalation.** Every action the key's roles grant must be one the
  creator holds (RE-E704, as for role assignments), and the roles must exist.
  Administrators can mint any key.
- **Tenant.** A key acts in its creator's tenant. Cross-tenant callers
  (administrators) name one with `tenant`.
- **Fixed roles.** `PATCH` changes only the name and the expiry. To change
  roles, create a new key and revoke the old one.

Who may manage keys is a normal permission: `authz_api_keys:create`, `:list`,
`:get`, `:update`, `:transition:revoke`. These are sensitive actions:
wildcards don't match them. A policy can let users manage their own keys with
a condition on `resource.created_by`.

## Using a key

Send it as `Authorization: Bearer rek_…`, or as `X-API-Key: rek_…` for
clients that can't set `Authorization`. Over the GraphQL WebSocket, put it in
`connection_init` like a token. The caller is a principal `apikey:<id>` with
`service=True`, the key's roles (application roles, used as they are) and its
tenant, so permissions, row conditions, tenancy, the audit trail and the access
log treat it like any caller.

`ApiKeyAuthenticator` refuses an unknown or forged key ("Invalid API key"), a
revoked one and an expired one with RE-E701 (401). It updates `last_used_at`
at most once a minute per key and instance (`touch_every`). Tokens that don't
start with `rek_` go to `fallback`. Without a fallback they're refused.

## Revoking

`POST /authz_api_keys/{id}/transitions/revoke` stops the key at once, on every
instance, since each request reads the key. Revocations and creations are in
the audit trail and in `GET /authz_api_keys/{id}/history`.

## Guessing

With a `RateLimiter` (`bearer_actor(keys, rate_limiter=...)`, `failed_auth`),
an address that fails authentication too often gets 429s before its keys are
even checked. See [rate limits](rate-limits.md).

## Practice

- One key per client and purpose, with an expiry. Rotate by creating the new
  key, deploying it, then revoking the old one.
- Give keys the narrowest role. A dedicated custom role per integration makes
  its grants explicit.
- Keys are secrets: environment or secret manager, never a repository or a
  URL. The `rek_` prefix lets secret scanners recognise them.
