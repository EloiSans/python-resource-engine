# Example app

`examples/invoices` runs as a complete back end:
- REST, OpenAPI and AsyncAPI over SQLite or PostgreSQL;
- bearer tokens from Keycloak;
- policies seeded at start-up;
- every module record (customers, invoices, tags and their links) confined to
  the caller's organization.

## Run it

```bash
docker compose -f compose.emulators.yaml up -d keycloak        # imports the invoices and partner realms
uv run uvicorn invoices.app:app --app-dir examples --reload    # http://127.0.0.1:8000/docs
```

Get a token for a demo user, then call the API:

```bash
TOKEN=$(curl -s http://127.0.0.1:8180/realms/invoices/protocol/openid-connect/token \
  -d grant_type=password -d client_id=invoices-dev-tests -d client_secret=invoices-dev-secret \
  -d username=eli -d password=dev -d scope="openid organization" \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/invoices
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"customer": "ACME", "amount": "120.50"}' http://127.0.0.1:8000/invoices
```

The realm follows OAuth 2.1, and has two clients:

- **`invoices-web`:** the front end's public client. It allows only the
  authorization code flow with PKCE (S256), with redirects to
  `http://localhost:*`. The password grant is refused there.
- **`invoices-dev-tests`:** a confidential client used only so `curl` and the
  tests can sign in as the demo users with the password grant. It exists in
  this development realm alone; never create such a client in a real realm.

- **Without Keycloak:** every request is a 401.
- **Elsewhere:** `KEYCLOAK_URL` and `KEYCLOAK_REALM` point the app at another
  Keycloak.
- **Database:** `DATABASE_URL` chooses it (SQLite `invoices.db` by default).
  The app creates the tables at start-up; production uses the migrations
  instead (see [migrations](migrations.md)).

## The realm

| User (password `dev`) | Organization | Realm role | Engine role | Can |
|---|---|---|---|---|
| `eli` | ACME | `issuer` | `issuer` | create and run invoices, customers, tags |
| `pau` | ACME | `approver` | `approver` | see pending invoices, approve and reject under 10,000 |
| `ada` | ACME | `accountant` | `viewer` (system role) | read everything in ACME, write nothing |
| `gina` | Globex | `issuer` | `issuer` | the same as `eli`, in Globex |
| `root` | none | `invoices-admin` | `admin` (system role, cross-tenant) | everything, in every organization |

Keycloak Organizations hold the tenants: the token's `organization` claim lists
the user's organizations. The app's authenticator uses the Keycloak preset:

```python
keycloak(KEYCLOAK_URL, "invoices", audience="invoices-api", tenant_claim="organization")
```

- **Roles:** realm roles become the principal's roles, and
  `invoices.bootstrap.authz` maps them to engine roles.
- **Tenant:** a user in exactly one organization gets it as `Principal.tenant`.
  Users in several organizations get none, so tenant-scoped resources refuse
  them (RE-E708). Give such users one organization per token, for example
  with a client per organization or a token exchange.
- **Audience:** the `invoices-web` client puts `invoices-api` in `aud`, which
  the app requires.

Approving an invoice whose registered customer has an email address sends
it an email in Catalan, through the worker and Mailpit
(`docker compose -f compose.emulators.yaml up -d mailpit`, UI at
http://127.0.0.1:8026); see [email](email.md).

Initech's users (`sara`, `tom`) sign in through SAML at a partner realm
federated into `invoices`; see [SAML sign-in](saml.md).

To add an organization or a user, use the admin console at
`http://127.0.0.1:8180` (admin / admin): add the user to an organization and
give them a realm role. For the realm the containers import, edit
`docker/keycloak/invoices-realm.json`; Keycloak imports it only when the
realm doesn't exist yet.

## Tenancy in the example

`invoices.bootstrap.build_registry(tenant_scoped=True)` registers the module's
resources with `tenant_field = "organization"`, and the app serves that
registry (`tenant_registry`):

- **Listing:** each user lists only their organization's records. Another
  organization's record is a 404 (see [tenancy](tenancy.md)).
- **Creating:** a new record gets the caller's organization. Naming another
  with `?tenant=` is RE-E709.
- **References:** they don't cross organizations. An ACME invoice can't point
  to a Globex customer (RE-E108).
- **Platform admins:** `root` sees every organization, and names one with
  `?tenant=globex` to create records there.
- **Uniqueness:** tax ids and tag names are unique per organization (unique
  keys on `organization` plus the field).

`registry` (without tenancy) remains for the engine's own tests, which use the
example as a fixture.

## Tests

- **`tests/test_invoices_http_sql.py`:**
  - runs the app over HTTP and SQLite with tokens shaped like Keycloak's,
    signed with a local key (`invoices.devtokens.Tokens`);
  - the app verifies them as real ones (signature, issuer, audience, expiry),
    so it runs without Keycloak;
  - checks 401s, isolation between organizations, cross-tenant admins and
    roles from the token.
- **`tests/test_invoices_keycloak.py`:** takes real tokens from the compose
  Keycloak for `eli`, `gina`, `ada`, `pau` and `root`, and runs the same flows
  end to end (marker `emulator("keycloak")`; skipped when Keycloak is down,
  required in the emulator CI job).
