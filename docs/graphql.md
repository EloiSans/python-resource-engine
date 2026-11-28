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
- WebSocket `/graphql` serves subscriptions when a hub is given (see
  [Subscriptions](#subscriptions)).

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
- **Exports:** with a blob store, `exportInvoices(format, orderBy, filter)`
  queues a background export and `export(id)` follows it (see
  [exports](exports.md#graphql-and-mcp)).

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

- **Cost limit:** each field costs 1 plus its selections, and a field with a
  `limit` argument (a page) multiplies its selections by that limit, or by its
  default when the query leaves it out. So
  `customers(limit: 10) { items { invoices(limit: 10) { items { id } } } }`
  costs 1 + 10 × (1 + 1 + 10 × 2) = 221. Operations above `max_cost`
  (default 10,000) are refused before they run, with code
  `QUERY_TOO_COSTLY`. Fragments count, introspection doesn't.
  `query_cost(schema, document, operation_name, variables)` gives the number,
  for logs or tests.

## Persisted queries

`persisted=PersistedQueries(...)` turns on
[automatic persisted queries](https://www.apollographql.com/docs/apollo-server/performance/apq)
(Apollo's protocol, supported by Apollo Client, urql and graphql-request
links). The client sends the query's SHA-256 instead of its text:

1. `{"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "…"}}}`.
2. The first time the server answers `PersistedQueryNotFound`
   (code `PERSISTED_QUERY_NOT_FOUND`), and the client sends the text and the
   hash together. The server checks the hash
   (`PERSISTED_QUERY_HASH_MISMATCH` otherwise) and remembers the query.
3. From then on the hash is enough, with `POST`, or with `GET` and
   `extensions` as a JSON parameter, which CDNs can cache.

The learned queries are kept in memory, the `cache_size` most recently used
(default 1000), per process.

**Allow-list.** For first-party clients, extract the operations at build time
(GraphQL Code Generator's persisted documents, or `persistgraphql`) and run
only those:

```python
from resource_engine_graphql import PersistedQueries

known = json.loads(Path("persisted-queries.json").read_text())  # {sha256: query}
mount_graphql(app, registry, manager, persisted=PersistedQueries(known, allow_only=True))
```

Arbitrary query text is then refused with `PERSISTED_QUERY_REQUIRED`, and
unknown hashes with `PERSISTED_QUERY_NOT_FOUND`. The schema itself is
unchanged, and so are the permissions: the allow-list limits what can be
asked, not who may ask it.

## Subscriptions

With an `EventHub`, the schema gains a `Subscription` type: one field per
resource that has `get`, named `<singular>Changed`.

```graphql
subscription {
  invoiceChanged(id: 7, events: ["TransitionExecuted"]) {
    event      # ResourceCreated, ResourceUpdated, TransitionExecuted, ResourceDeleted...
    id
    occurredAt
    record { id status amount }   # as the subscriber reads it now; null once deleted
  }
}
```

`id` follows one record and `events` keeps only some event types. Resources
with tenancy also take `tenant`.

**Permissions.** Subscribing needs `get` on the resource, or on the record
with `id`. Each event is then re-read with `manager.get` as the subscriber, so
row rules, field rules and tenancy apply to every message. Records the
subscriber can't see are skipped, and a `ResourceDeleted` is sent only for
records this subscription has already delivered, so ids of hidden records
never leak. Because the record is read when the event arrives, it shows the
state at that moment: two quick changes can both show the second state.

**Feeding the hub.** The hub is an in-process `MessagePublisher`
(`resource_engine.messaging.EventHub`); give it the committed events:

```python
from resource_engine.messaging import EventHub, FanoutPublisher, OutboxRelay
from resource_engine.messaging.delivery import TransportEventPublisher

hub = EventHub()

# without an outbox: straight after each commit
manager = ResourceManager(..., event_publisher=TransportEventPublisher(hub))

# with the outbox: the relay publishes to the broker and the hub
relay = OutboxRelay(uow_factory, FanoutPublisher(broker, hub))

mount_graphql(app, registry, manager, hub=hub, authenticator=oidc)
```

With several API instances, each one only sees the events its own relay
publishes. Instead, let every instance consume a broadcast topic (a
subscription per instance) into its hub:
`MessageConsumer(source, MessageProcessor(hub.router(), name="graphql-live"))`.

Each subscriber has a queue of `queue_size` events (default 100). A
subscriber that falls behind loses its oldest events, so one stuck client
never holds back the others; refetch after reconnecting.

**The WebSocket.** Subscriptions are served on the same path over WebSocket,
with the [`graphql-transport-ws`](https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md)
protocol (the `graphql-ws` client, Apollo Client, urql). Browsers can't set
headers on a WebSocket, so the token goes in `connection_init`:

```ts
import { createClient } from "graphql-ws";

const client = createClient({
  url: "wss://api.example.com/graphql",
  connectionParams: async () => ({ authorization: `Bearer ${await getToken()}` }),
});
```

`authenticator` checks it (the same one as `bearer_actor`), and
`auth_required=False` lets anonymous sockets in. Queries and mutations also
work on the socket. The server closes with 4401 when `connection_init` doesn't
come first, 4403 for a missing or invalid token, 4408 when it doesn't come
within 10 seconds, 4409 for a duplicate operation id, 4429 for a second
`connection_init` and 4400 for messages it can't read. Tokens expire: the
socket keeps the caller it authenticated, so reconnect with a fresh token
when you refresh it. Over HTTP, a subscription operation is refused.

`GraphQLAdapter.subscribe(query, variables, actor=...)` yields the results
without a socket (tests, server-sent events).

## Not included (yet)

- **Relay connections and global ids:** lists are offset pages.
- **Document uploads:** use the REST document routes.
