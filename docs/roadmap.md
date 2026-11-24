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

## Next

Ordered by priority. The first block finishes the MVP of the
[proposal](proposal-v3.md#12-abast-proposat-per-a-una-primera-versió-mvp).

### MVP: AI assistance and verification

- Scaffolding (`resource-engine scaffold resource|action|state-machine`).
- Skill evaluation: reference tasks run by an agent with and without the skill,
  scored by `check`, `docs validate` and the tests.

### Next

- Idempotency keys for sensitive operations (`Idempotency-Key` on HTTP writes,
  stored results).
- Alembic support: migrations for the engine's tables (outbox, inbox, audit,
  seals, permissions, documents, locks) and autogenerate-friendly metadata.
- Observability: structured JSON logs, operation duration metrics, OpenTelemetry
  metrics for job runs.
- Transition history view per record (from the audit trail).
- `resource-engine mcp`: the CLI's inspect, check, docs and scaffold tools as an
  MCP server.
- Choose and add a licence; CI publishing per package.

### Later

- Example app with authentication (Keycloak) so `invoices` can be tenant-scoped.
- Policy engines: OpenFGA conditions on input values, OPA bundles, Cedar schemas
  generated from the registry.
- Audit: external sinks (OpenTelemetry logs, SIEM); retention per resource or outcome.
- EventBridge and Event Grid publishers.
- SAP: OData v2/v4 repository for S/4HANA APIs on top of the REST repository,
  BTP destination service lookup.
- Composite unit of work (SQL resources and remote resources in one operation) and
  compensation hooks for remote writes.
- Azure Blob Storage and Google Cloud Storage blob stores; reference document
  processors (ClamAV scan, text extraction).
- NoSQL repositories (DynamoDB, Cosmos DB, Firestore) after adding capability flags.
