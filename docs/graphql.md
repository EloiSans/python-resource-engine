# GraphQL

`resource-engine-graphql` generates a GraphQL schema from the registry and
serves it next to the REST API. Every resolver is a `ResourceManager` call made
as the caller, so permissions, row and field rules, tenancy, validation, hooks,
events and the audit trail behave exactly as over REST.

```python
from resource_engine_fastapi import bearer_actor, mount_resources
from resource_engine_graphql import mount_graphql

mount_resources(app, registry, manager, actor_dependency=bearer_actor(oidc))
mount_graphql(app, registry, manager, actor_dependency=bearer_actor(oidc))
```

The server answers on these routes:

- `POST /graphql` runs queries and mutations, with
  `{"query", "variables", "operationName"}`.
- `GET /graphql?query=...` runs queries only.
- `GET /graphql/schema.graphql` returns the schema in SDL. Point your client
  code generator (GraphQL Code Generator, Apollo, urql, gql) at it.

`GraphQLAdapter(registry, manager).execute(query, variables, actor=...)` runs a
request without HTTP (tests, other transports), and `.sdl()` prints the schema.

## The schema

For the resource `invoices`:

```graphql
type Invoice {
  id: Int!
  customer: String
  amount: Decimal
  issueDate: Date
  customerId: Int
  customerRecord: Customer                          # reference
  tags(offset: Int = 0, limit: Int = 50, orderBy: [String!], filter: TagFilter): TagPage
  allowedTransitions: [String!]
  history: [StateChange!]
}

type Query {
  invoice(id: Int!): Invoice
  invoices(offset: Int = 0, limit: Int = 50, orderBy: [String!], filter: InvoiceFilter): InvoicePage!
}

type Mutation {
  createInvoice(data: InvoiceCreateInput!, idempotencyKey: String): Invoice
  updateInvoice(id: Int!, data: InvoiceUpdateInput!, expectedVersion: Int, idempotencyKey: String): Invoice
  deleteInvoice(id: Int!, expectedVersion: Int, idempotencyKey: String): Boolean
  submitInvoice(id: Int!, expectedVersion: Int, idempotencyKey: String): Invoice
  rejectInvoice(id: Int!, data: RejectInvoiceInput!, ...): Invoice
  recalculateTotalsInvoice(id: Int!, data: ApplyDiscountInput!, ...): Invoice
}
```

- **Names:**
  - fields are camelCase (`issueDate` for `issue_date`);
  - types are the singular PascalCase of the resource (`Invoice`);
  - inputs are named after their Pydantic schema plus `Input`;
  - the single-record query is the singular (`invoice`), or `<plural>ById`
    when the two match;
  - mutations are `create`, `update` and `delete` + type, then
    `<event><Type>` for transitions and `<action><Type>` for actions.

  Two names that collide refuse to build, so rename the transition or action.
- **Types:**
  - decimals are the `Decimal` scalar, sent as strings (`"120.50"`);
  - dates are `Date` and timestamps `DateTime`, both ISO 8601;
  - UUIDs are `UUID`;
  - dicts and unions are `JSON`;
  - nested Pydantic models get their own types.
- **Nullable fields:** every field except `id` is nullable. Fields hidden by
  field permissions come back as `null`; in REST they are left out. A front
  end should treat `null` on a restricted field as "not visible".
- **Lists:** they return offset pages, `{items total offset limit}`, as over
  REST. `orderBy` takes camelCase field names with an optional leading `-`.
- **Filters:** `filter` is typed, with equality on the filterable fields, and
  a related record's filterable fields one level deep:

  ```graphql
  invoices(filter: {status: "draft", customerRecord: {country: "ES"}}) { total }
  ```
- **Tenants:** tenant-scoped resources take `tenant` on queries and mutations,
  for cross-tenant callers only.

## Relations

- **References** (`customerRecord`) are batched per request: all the invoices
  of a page load their customers in one `manager.get_many` call. That is one
  query per relation per level, with the caller's `get` permission. A record
  the caller may not read is `null`.
- **Reverse relations and many-to-many links** (`invoices` on a customer,
  `tags` on an invoice):
  - they take their own `offset`, `limit`, `orderBy` and `filter`, and run
    `manager.list_related` once per parent;
  - they are `null` when the caller cannot list the related resource;
  - ask for small pages on lists of parents.
- **Linking and unlinking** many-to-many records is the join resource's
  mutations (`createInvoiceTag`, `deleteInvoiceTag`). See
  [relationships](relationships.md).

## Errors

Engine and domain errors keep their code, so clients branch on
`extensions.code` exactly as they do on REST's `error.code`:

```json
{"data": {"invoice": null},
 "errors": [{"message": "invoices 999 does not exist", "path": ["invoice"],
             "extensions": {"code": "RE-E102", "doc": "docs/errors.md#re-e102", "details": {"resource": "invoices", "id": 999}}}]}
```

- **Partial results:** errors are per field, so the rest of the response is
  still served.
- **Status codes:** HTTP is 200 for every GraphQL request, as GraphQL over
  HTTP specifies; 400 for a body that is not a GraphQL request; 405 for a
  mutation sent with `GET`.
- **Unexpected exceptions:** they are logged on `resource_engine.graphql` and
  come back as `Internal error` with code `INTERNAL`.

## Protection

- **Depth limit:** operations nested deeper than `max_depth` fields (default
  10) are refused before they run, with code `QUERY_TOO_DEEP`.
  Introspection fields don't count, so GraphiQL and code generators work.
- **Other limits:** page sizes are capped by each resource's `max_page_size`,
  and related lists by their own `limit`.
- **Credentials:** use the same `actor_dependency` as REST, so a GraphQL
  request carries the same bearer token and gets the same permissions.

## Not included (yet)

- **Subscriptions:** events go to brokers. See [AsyncAPI](asyncapi.md) and the
  [front-end guide](frontend.md#live-updates).
- **Relay connections and global ids:** lists are offset pages.
- **Document uploads:** use the REST document routes.
- **Persisted queries and a query cost analysis** beyond the depth limit.
