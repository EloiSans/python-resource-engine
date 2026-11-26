# Front-end guide

The HTTP API that `mount_resources` generates follows the same conventions for
every resource. A front end can rely on them instead of learning each endpoint,
and can generate its client from the OpenAPI document.

```text
GET  /openapi.json      every route, schema and parameter (FastAPI's /docs renders it)
GET  /asyncapi.json     the events the backend publishes (with mount_asyncapi)
GET  /authz/me          what the signed-in user may do (with an authorizer)
```

Prefer GraphQL? `mount_graphql` serves the same resources at `/graphql`, with
the same permissions and error codes (see [GraphQL](graphql.md)).

Generate types and a client from `/openapi.json` (for example
`openapi-typescript` or `orval`) and regenerate them in CI. The schemas come
straight from the backend's Pydantic models, so a breaking change shows up as a
type error.

## Routes

For a resource `invoices`, only the operations it enables exist:

| Request | Does | Success |
|---|---|---|
| `GET /invoices` | List (paged, filtered, sorted) | 200, a page |
| `GET /invoices/{id}` | Read one record | 200, the record (`ETag` when versioned) |
| `POST /invoices` | Create | 201, the record |
| `PATCH /invoices/{id}` | Change the fields sent; the others keep their value | 200, the record |
| `DELETE /invoices/{id}` | Delete | 204, no body |
| `GET /invoices/{id}/transitions` | Transitions the user may run now | 200, `["submit", ...]` |
| `POST /invoices/{id}/transitions/{event}` | Run a transition (body if it takes input) | 200, the record |
| `GET /invoices/{id}/history` | Creation and transitions, oldest first | 200, a list |
| `POST /invoices/{id}/actions/{name}` | Run a record action | 200, its result |
| `POST /invoices/actions/{name}` | Run a collection action | 200, its result |
| `GET /customers/{id}/invoices` | Records pointing to this one ([reverse relation](relationships.md#reverse-relations)) | 200, a page |
| `GET /invoices/{id}/documents` | Attached documents | 200, a list |

- **Field names:** JSON keys are the schema field names, in snake_case.
- **Formats:**
  - decimals are strings (`"120.50"`), so parse them with a decimal library
    rather than as floats;
  - dates are ISO 8601 (`"2026-11-25"`), and timestamps are ISO 8601 with a
    time zone.

## Lists

```http
GET /invoices?offset=40&limit=20&order_by=-issue_date,id&status=draft&customer_record.country=ES&expand=customer_record
```

```json
{"items": [...], "total": 134, "offset": 40, "limit": 20}
```

- **Paging:** `offset` and `limit`; `limit` defaults to 50 and has a
  per-resource maximum (`max_page_size`). `total` counts every match, so
  `total > offset + items.length` means there are more pages.
- **Ordering:** `order_by` takes comma-separated fields, with `-` for
  descending. Only the resource's sortable fields are accepted.
- **Filters:** equality on the filterable fields, one query parameter each
  (`status=draft`). `<relation>.<field>` filters by a related record
  (`customer_record.country=ES`). The OpenAPI document lists exactly which
  parameters each list accepts.
- **Visibility:** users see only the records they may see. Row permissions
  and tenancy are applied by the backend, so `total` is already their count.
  Never filter for permissions on the client.
- **Errors:** an unknown filter, sort field or relation is a 400 with code
  `RE-E103`.

## Related records

| Want | Ask | Get |
|---|---|---|
| The customer of an invoice | `GET /invoices/7?expand=customer_record` | `"customer_record": {...}`, or `null` if the user may not read it (the id stays in `customer_id`) |
| A customer with its first invoices | `GET /customers/3?expand=invoices:5` | `"invoices": {"items": [...], "total": 42, "limit": 5, "has_more": true}`, or `null` |
| All of a customer's invoices, paged | `GET /customers/3/invoices?offset=0&limit=20` | A normal page |

- **Several at once:** `expand=customer_record,invoices`.
- **Nested:** `expand=invoices.customer_record` or `customer_record.invoices:5`,
  up to three levels.
- **Many-to-many:** `GET /invoices/7/tags` or `expand=tags` work like reverse
  relations. Link and unlink by creating and deleting the join records
  (`POST /invoice_tags`, `DELETE /invoice_tags/{id}`).
- **Expanded records:** they follow the same field rules as any other read.
- **Choosing between them:** embed short lists you always show with the
  parent. Call the paged route for lists that grow, or that the user filters
  or scrolls. See [relationships](relationships.md).

## Writing

```http
POST /invoices
Content-Type: application/json
Idempotency-Key: 6f1c2e4a-...

{"customer": "ACME", "amount": "120.50", "customer_id": 3}
```

- **Send only what changes.** `PATCH` updates the fields present in the body.
  A field sent as `null` is set to null, and fields that are not sent keep
  their value.
- **State fields aren't editable.** You can't set `status`; run the
  transition instead (`POST /invoices/7/transitions/submit`). Build the action
  buttons from `GET /invoices/7/transitions`: it already applies the user's
  permissions and the record's state.
- **Retry safely.** Send an `Idempotency-Key` (a fresh UUID per user intent)
  on every `POST`, `PATCH` and `DELETE`. A retry with the same key, after a
  timeout or double click, returns the first result instead of acting twice.
  Reusing a key for a different request is a 422, code `RE-E107`. See
  [idempotency](idempotency.md).
- **Don't overwrite other people's changes.** Versioned resources answer
  reads and writes with `ETag: "3"`. Send it back as `If-Match: "3"` when
  writing:
  - if someone changed the record in between, the write is refused with 409
    (`RE-E105`), so reload it and show the conflict;
  - resources that require it answer 428 (`RE-E106`) when `If-Match` is
    missing.

  See [concurrency](concurrency.md).
- **References must exist.** Setting `customer_id` to a customer that doesn't
  exist, or that the user may not see, is a 422 (`RE-E108`). Deleting a record
  that others still reference is a 409 (`RE-E109`) naming the referencing
  resource.

## Errors

Every error has the same body:

```json
{"error": {"code": "RE-E201", "message": "Transition 'approve' is not allowed for 'invoices' in state 'draft'",
           "hint": "Allowed transitions from 'draft': submit. ...", "doc": "docs/errors.md#re-e201",
           "details": {"state": "draft", "allowed": ["submit"]}}}
```

- **Branch on `code`.** It is stable and never reused. The message and hint
  are for developers and logs, in English.
- **Translate it yourself.** Show the user a text of your own for each code.
- **Validation errors** (422, `RE-E101`) carry Pydantic's list in `details`:
  `[{"loc": ["amount"], "msg": "...", "type": "decimal_parsing"}, ...]`. Map
  `loc` to form fields.
- **Business rule errors** raised by the domain have their own codes, such as
  `INVOICE`. They are 422s with the same body.

| Status | Codes | What the UI does |
|---|---|---|
| 400 | `RE-E103` invalid query | A bug in the client: fix the request |
| 401 | `RE-E701` not authenticated | Sign in again (`WWW-Authenticate: Bearer`) |
| 403 | `RE-E702` permission denied; `RE-E706` field not writable; `RE-E707` field not readable in a filter; `RE-E708`/`E709` tenant | Hide or disable the action; refresh `/authz/me` |
| 404 | `RE-E102` not found, or not visible to this user | Show "not found"; don't reveal more |
| 405 | `RE-E003` operation not enabled | A bug in the client |
| 409 | `RE-E201` transition not allowed; `RE-E105` stale version; `RE-E109` still referenced; `RE-E605`/`E606` documents | Reload and explain the conflict |
| 413 / 415 | `RE-E603` too large; `RE-E602` content type not allowed | Validate before uploading |
| 422 | `RE-E101` validation; `RE-E107` idempotency key reused; `RE-E108` reference not found; domain codes | Show the errors on the form |
| 428 | `RE-E106` version required | Send `If-Match` |
| 5xx | `RE-E501` upstream service, `RE-E607` storage, `RE-E000` internal error | Generic error; safe to retry with the same `Idempotency-Key`. Show the `X-Request-Id` response header so support can find the failure |

The full list is in the [error catalogue](errors.md).

## Signing in and tenants

- **Tokens:** send `Authorization: Bearer <access token>` from your identity
  provider (Keycloak, Cognito, Entra ID, Google) on every request. A missing,
  expired or invalid token is a 401. See [identity](identity.md).
- **Tenant-scoped resources:** they show only the user's tenant, with nothing
  to send. Cross-tenant users (support, admins) pick a tenant with
  `?tenant=<id>`. See [tenancy](tenancy.md).

## Permissions in the UI

```http
GET /authz/me
```

```json
{"principal": "keycloak:ada", "roles": ["accountant"],
 "actions": {"invoices:list": "allowed", "invoices:get": "allowed", "invoices:transition:approve": "conditional"},
 "fields": {"invoices": {"hidden": ["notes"], "readonly": ["amount"], "conditional": []}}}
```

- **Showing actions:** show an action when it is `allowed` or `conditional`.
  `conditional` depends on the record (an amount limit, the owner), so the
  backend decides when the user tries. A refusal is a 403 to handle
  gracefully.
- **Per record:** use `GET /invoices/{id}/transitions` for the exact
  transitions a user may run on this record now.
- **Hidden fields:** fields the user may not read are *absent* from responses,
  not `null`. Treat a missing key as "not visible", and don't render an empty
  value as if it were data. `fields.hidden` and `fields.readonly` let forms
  hide or disable inputs before the user tries.
- **Action names** follow `<resource>:<operation>`, `<resource>:transition:<event>`
  and `<resource>:action:<name>`. See [permissions](permissions.md).

## Documents

| Request | Does |
|---|---|
| `POST /invoices/{id}/documents/{slot}` (multipart, field `file`) | Upload through the API |
| `POST /invoices/{id}/documents/{slot}/uploads` | Get a presigned URL for large files: `PUT` the file there, then `POST /documents/{document_id}/confirm` |
| `GET /documents/{document_id}/content` | Download (or a 307 to a presigned URL) |
| `DELETE /invoices/{id}/documents/{document_id}` | Remove |

Each slot declares its content types, maximum size and whether it holds one
file or many; the OpenAPI document and the generated functional docs list
them. A slot with processing (a virus scan, say) leaves the document
`processing` until it is `available` or `rejected`, so show that state. See
[documents](documents.md).

## History and audit

`GET /invoices/{id}/history` gives the record's timeline, with who did what
and when:

```json
[{"at": "2026-11-25T09:12:00+00:00", "operation": "create", "event": null, "from_state": null, "to_state": "draft", "actor": "keycloak:ada", "correlation_id": "..."}]
```

It is readable by whoever can read the record. The full audit trail
(`/audit_log`) is restricted to auditors. See [audit](audit.md).

## Live updates

The backend publishes events to its broker after each committed change. It
does not push them to browsers. To refresh screens, a backend-for-frontend or
a small service consumes them (see [contracts](contracts.md#consumer-scaffold-from-the-providers-document))
and forwards them over WebSocket or SSE. `/asyncapi.json` describes every
event and its payload.

## Checklist

- [ ] Client and types generated from `/openapi.json` in CI.
- [ ] An `Idempotency-Key` (a fresh UUID per user action) on every write.
- [ ] `If-Match` with the last `ETag` on writes to versioned resources; 409 handled.
- [ ] Errors handled by `code`; 422 `details` mapped to form fields.
- [ ] Action buttons from `/transitions` and `/authz/me`, never hard-coded roles.
- [ ] Missing keys treated as hidden fields, not as empty values.
- [ ] Decimals handled as strings or decimals, never floats.
- [ ] Lists paged with `offset`, `limit` and `total`; long related lists through their own route.
