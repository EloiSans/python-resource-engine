# Roadmap

## Done (0.1.0.dev0)

- Declarative `Resource`, validated at registration; `ResourceRegistry`.
- `ResourceManager` with the [lifecycle contract](lifecycle.md): CRUD, transitions,
  actions, sync/async hooks, rollback, post-commit events.
- `python-statemachine` 3 integration (sync and async machines, payload to callbacks).
- Ports + in-memory adapters; SQLAlchemy async adapter (imperative mapping).
- FastAPI adapter: generated routes, OpenAPI, error mapping, `actor_dependency` hook.
- Devtools: introspector, documentation model, Markdown/Mermaid/JSON/`ai_context`
  generators, docstring coverage validator, `inspect`/`check`/`docs`/`ai context`
  CLI with `--json`.
- Reference module `examples/invoices`, tested over memory, SQLite and HTTP.
- Messaging: `EventEnvelope`, direct and transactional-outbox delivery, `OutboxRelay`,
  `EventRouter` for consumers, outbox for in-memory and SQLAlchemy.
- Contract test suite for adapters (`resource_engine.testing.contracts`).
- Cloud publishers: SQS, SNS, Service Bus, Pub/Sub ([catalogue](adapters.md)).
- SAP publishers: Event Mesh (REST, CloudEvents) and advanced event mesh (Solace REST).
- Consumers: `MessageSource` port, `MessageProcessor` (retry/backoff, dead-letter,
  per-key ordering), `MessageConsumer` loop, inbox (in-memory, SQLAlchemy), push
  endpoint in FastAPI; sources for SQS, Pub/Sub, Service Bus and Event Mesh.
- HTTP adapters: REST and SOAP repositories, webhook publisher, OAuth2 client
  credentials; CloudEvents mapping in the core; `RemoteCallFailed` (RE-E501).

- Documents: `BlobStore` port, built-in `documents` resource with state machine,
  slots (`required_for`, `processing`), API and presigned uploads, staged effects
  (`ctx.on_rollback` / `on_commit`), processing pipeline, janitor; S3 adapter on
  Garage; SQLAlchemy table; FastAPI routes; devtools support.
- RabbitMQ publisher and consumer source (quorum queues, DLX).
- Identity: `Principal` / `Authenticator`, OIDC verification with Keycloak, Cognito,
  Entra ID, Google Identity Platform and Google ID token presets; `bearer_actor`.
- Local stack with UIs: Garage, RabbitMQ (:15672), Keycloak (:8180).
- Permissions: system roles in code, ABAC policies as data managed from front ends
  (`authz_*` resources, catalogue, `/authz/me`, `/authz/explain`), checks at steps
  2 and 5b, row-level filters compiled to SQL, no privilege escalation, role
  sources (identity provider, stored, combined), permission matrix in the docs.
- Field-level permissions: `<resource>:read|write:<field>` actions, restricted
  fields (code or front end), hidden fields omitted, protected writes rejected
  (RE-E706), no filtering or sorting by hidden fields (RE-E707).
- Audit trail: `AuditSink` port; read-only `audit_log` resource written in the
  same transaction as each change; refused attempts; opt-in reads; masking of
  restricted and excluded fields; SQLAlchemy table; logging and composite sinks.
- Tenancy: `tenant_field` confines every operation to `Principal.tenant`
  (independent of policies); cross-tenant admin and engine jobs; `?tenant=`.
- External policy engines: `EngineAuthorizer` and the `PolicyEngine` port; OPA
  (compile API row filters), Cedar (partial evaluation) and OpenFGA
  (`list_objects`, `TupleSync`) adapters; `AuthorizerContract`.
- Optimistic concurrency: `version_field` on resources, compare-and-set in the
  in-memory and SQLAlchemy repositories, `ETag` / `If-Match` over HTTP (409,
  428 with `require_version`), refused writes in the audit trail.
- `manager.transaction()`: several operations in one unit of work, post-commit
  work deferred, doomed on failure. Consumers: handler and inbox in one
  transaction (`MessageProcessor(manager=...)`, exactly-once effects),
  concurrency per key, `MessageObserver` metrics hooks with `LoggingObserver`.
- Messaging retention (`MessagingJanitor`: inbox marks, published outbox
  records) and OpenTelemetry consumer metrics (`resource-engine-opentelemetry`).
- Audit tamper evidence (`AuditSealer`: chained seals over batches of entries,
  optional HMAC with key rotation, `verify()`) and retention (`AuditJanitor`,
  keeping seals of purged batches).
- Background jobs: `JobRunner` (periodic jobs, services, observers), `LockProvider`
  port with SQL leases and in-memory locks, `resource-engine worker` command.
- Tracing: `Tracer` port with spans for operations, outbox publishing, consumers
  and jobs; W3C trace context on envelopes (JSON, CloudEvents); `OtelTracer`.
- `check`: hook/effect mismatches (RE-C002 effects inside the transaction, RE-C003
  `after_commit` hooks that need it).
- AI skill packaged with devtools (`SKILL.md`, references, anti-patterns,
  `llms.txt`, the reference module kept in sync by a test) and `resource-engine
  ai install` / `ai sync` (Claude skill, managed `AGENTS.md` and Copilot blocks,
  Cursor rule, lock file, `--check` for CI).
- `check`: operations only `admin` can perform, against the seed policies
  (RE-C004); declarations the engine refuses reported as violations (RE-C005).
- `resource-engine scaffold resource|action|state-machine`: modules that pass
  `check` and their own tests, TODO docstrings flagged by `docs validate` (RE-D010).
- Skill evaluation (`evals/`): reference tasks run by an agent with and without
  the skill, scored by `check`, `docs validate --strict`, the project's tests and
  hidden tests; reference solutions keep the harness honest in CI.
- Apache-2.0 licence for every package; CI on Python 3.12–3.14 with an emulator
  job that fails when a service is down; per-package releases to PyPI from
  `<distribution>/v<version>` tags with trusted publishing.
- Migrations: `engine_tables` and `NAMING_CONVENTION` for Alembic autogenerate,
  `resource-engine db init`, `run_migrations` (async, SQLite batch mode), tested on
  SQLite and PostgreSQL; the SQL contracts also run on PostgreSQL.
- Idempotency keys on every write (`idempotency_key=`, `Idempotency-Key` header):
  results stored in the operation's transaction, per caller, replayed on retries,
  races resolved by the store's key; `IdempotencyContract`, `IdempotencyJanitor`.
- Observability: log context (operation, consumer, job, correlation and trace
  ids) on every record, `JsonFormatter` and `configure_logging`, `worker
  --log-format json`; `OperationObserver` with `OperationMetrics`, and
  `JobMetrics` for job runs.
- Transition history per record from the audit trail (`manager.history`,
  `GET /<resource>/{id}/history`), readable by whoever can read the record;
  create entries now record the initial state.
- MCP server (`resource-engine-mcp`): every operation as a tool (CRUD,
  transitions, actions, allowed transitions, history) with schemas from the
  Pydantic models and descriptions from the docstrings; tools listed per
  caller, calls through the manager as the caller; stdio and Streamable HTTP
  with bearer tokens and OAuth protected resource metadata.
- AsyncAPI 3.0 document (`asyncapi_document`, `resource-engine docs asyncapi`,
  `mount_asyncapi`): published engine and declared domain events per resource,
  typed for the resource; consumed events per `EventRouter`; envelope or
  CloudEvents; servers and channels from configuration. `Resource.events` with
  `check` RE-C006 and docs RE-D011; typed consumer payloads
  (`router.on(..., payload=Model)`, invalid ones dead-lettered).
- Relationships (`Resource.references`): integrity on writes (RE-E108),
  `on_delete` restrict (RE-E109), `set_null` and `cascade` through the manager
  in one transaction; `expand` on `get`/`list` (batched, caller's permissions,
  unreadable records as null) and `<relation>.<field>` filters, over HTTP and
  MCP; shown in the generated docs; the example gains a `customers` resource.
- Reverse relations: `GET /customers/{id}/invoices` (`manager.list_related`,
  paged, the parent must be readable, the caller's list permissions) and
  capped inline `expand=invoices[:N]` with `total` and `has_more`;
  `related_name` / `reverse=False` on `Reference`. Front-end guide
  (`docs/frontend.md`).
- Nested expansion (`expand=invoices.customer_record`, three levels), inline
  reverse expansion on lists batched in one query, many-to-many links through
  a join resource (`link = ("invoice_id", "tag_id")`: `GET /invoices/{id}/tags`,
  `expand=tags`); the example gains `tags` and `invoice_tags`.
- GraphQL adapter (`resource-engine-graphql`): a schema generated from the
  registry (camelCase, offset pages, typed filters with related fields,
  mutations for writes, transitions and actions), references batched per
  request (`manager.get_many`), reverse and many-to-many fields with their own
  paging, engine error codes in `extensions`, a depth limit, `mount_graphql`.
- Example app with Keycloak: realm `invoices` (organizations ACME and Globex,
  demo users per role, a public PKCE client), the app verifies bearer tokens and
  runs tenant-scoped by organization with policies seeded at start-up;
  `keycloak(tenant_claim="organization")`; tests with locally signed tokens and
  end to end against the compose Keycloak.
- Timed transitions: `Resource.timers` (`Timer(event, after=, since=, states=,
  data=)`) and `state_since_field` stamped by the engine on every state change;
  `TimerJob` runs due transitions as `system:timers` in batches through the
  manager (guards, hooks, audit, events, history); in the generated docs; the
  example rejects invoices pending for 30 days.
- `resource-engine scaffold consumer`: payload models, a typed `EventRouter`
  and contract tests from a provider's AsyncAPI (3.0 or 2.x; envelope,
  CloudEvents or raw bodies). Contract testing on both sides:
  `assert_consumer_contract` against the provider's document (snapshot or
  latest) and `docs asyncapi --check` for breaking changes before publishing.
- Development MCP server (`resource-engine mcp`, extra `[mcp]`): catalogue,
  inspect, check, docs validation, AsyncAPI and its compatibility check,
  error explanations and the scaffold commands as tools, each run in a fresh
  process; the skill's guides and the live catalogue as resources.
- `ai install --mcp`: the development MCP server registered in `.mcp.json`,
  `.cursor/mcp.json` and `.vscode/mcp.json` (only its own entry managed),
  kept by `ai sync` and checked by `ai sync --check`.

## Next

Ordered by priority. The MVP of the
[proposal](proposal-v3.md#12-abast-proposat-per-a-una-primera-versió-mvp) is complete;
what follows builds on it.

### Soon

- GraphQL next steps: subscriptions from the outbox events, Relay connections,
  persisted queries and query cost limits.
- Process managers (sagas) for long-running processes across resources: react
  to events, keep their own state, issue manager operations, time out and run
  compensations; built on consumers, the outbox, jobs and leases.
- Several state machines per resource (an invoice's approval and payment
  states), each with its own field, transitions, permissions, routes and
  history.
- View resources: read-only resources backed by a query (aggregates,
  projections, joins such as invoices with their balance or totals per
  customer) with the same filters, sorting, permissions, `expand`, docs,
  GraphQL and MCP tools as entity resources.

### Later

- Versioning: versions of resource schemas, actions and events published side
  by side (`/v2/...`, `schema_version` per event type), deprecation notices in
  OpenAPI, AsyncAPI and GraphQL, and the contract checks comparing versions.
- Documentation site: the generated functional docs as a browsable static site
  (MkDocs or plain HTML) with the state diagrams and the permission matrix, for
  people who do not read the repository.
- Complete scaffolding: hooks, domain events (declared on the resource),
  document slots, references and seed policies, so every functional element
  can start from `resource-engine scaffold` and the development MCP server.
- Visual explorer (read-only): a local web UI with the resources, their state
  diagrams, relations, the permission matrix and a try-it console against a
  running app, for functional reviews; code stays the source of truth.

- SOAP server adapter (WSDL from the schemas), only for a concrete integration
  that needs it; the SOAP client side already exists.
- Policy engines: OpenFGA conditions on input values, OPA bundles, Cedar schemas
  generated from the registry.
- Audit: external sinks (OpenTelemetry logs, SIEM); retention per resource or outcome.
- EventBridge and Event Grid publishers.
- SAP: OData v2/v4 repository for S/4HANA APIs on top of the REST repository,
  BTP destination service lookup.
- Composite unit of work (SQL resources and remote resources in one operation) and
  compensation hooks for remote writes.
- Azure Blob Storage (`azure[blob]`) and Google Cloud Storage (`gcp[storage]`) blob stores; reference document
  processors (ClamAV scan, text extraction).
- NoSQL repositories (DynamoDB, Cosmos DB, Firestore) after adding capability flags.
