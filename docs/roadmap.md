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

## Next

Ordered by priority. The MVP of the
[proposal](proposal-v3.md#12-abast-proposat-per-a-una-primera-versió-mvp) is complete;
what follows builds on it.

### Soon

- Relationships between resources: declared references (`customer_id` →
  `customers`) with integrity checks, filters and expansion on `list`/`get`,
  permissions applied to the related records, shown in the generated docs.
- Consuming external AsyncAPI documents: payload models and handler stubs
  scaffolded from a provider's AsyncAPI (`resource-engine scaffold consumer`).
- Development MCP server (`resource-engine mcp`): the CLI's inspect, check,
  docs and scaffold tools for coding agents.

### Later

- GraphQL adapter (queries for `list`/`get`, mutations for writes,
  transitions and actions), after relationships between resources exist.
- SOAP server adapter (WSDL from the schemas), only for a concrete integration
  that needs it; the SOAP client side already exists.
- Example app with authentication (Keycloak) so `invoices` can be tenant-scoped.
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
