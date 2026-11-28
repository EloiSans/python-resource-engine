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
- Process managers: `Process` (a resource whose records are instances, one
  per `correlation` key) with `reacts = [On(event, handler, ...)]`; handlers get
  a `ProcessStep` (`start`, `transition`, `can`, the manager as
  `system:processes`); `ProcessRunner(manager).router()` run through
  `MessageProcessor(manager=)` (exactly once) from a broker or from the outbox
  with `ProcessFeed`; timeouts are timers, compensations explicit transitions;
  `internal_operations` keeps instance creation away from the APIs; in the
  generated docs. The example gains the invoice payment process and a worker.
- Error reporting: the `ErrorReporter` port gets every unexpected failure once
  (operations, HTTP/GraphQL/MCP 5xx, consumers, outbox, jobs, timers, after
  commit) with its context; `resource-engine-sentry` for Sentry, GlitchTip and
  Bugsink; access log with request ids (`mount_resources(access_log=True)`);
  outgoing-call logs and spans in the REST, SOAP and webhook adapters; failed
  operations in the audit trail (`audit_failures=True`).
- GraphQL subscriptions (`<singular>Changed`) over `graphql-transport-ws`, fed
  by an in-process `EventHub` (after commit, from the outbox with
  `FanoutPublisher`, or a broadcast topic per instance), each event re-read
  as the subscriber; query cost limits (`QUERY_TOO_COSTLY`); automatic
  persisted queries with an optional allow-list. Relay connections dropped:
  offset pages stay.
- Several state machines per resource (`machines = {"approval": Machine(...),
  "delivery": Machine(...)}`), each with its own field, since field, timers
  and history entries; transition names unique per resource, so routes,
  permissions, GraphQL and MCP keep the bare event; guards can read another
  machine's state; `TransitionExecuted.machine`; `allowed_transitions` runs
  guards. The example invoice gains a delivery machine.
- Polymorphic resources: a base (`discriminator = "type"`) lists and reads
  every kind with each subtype's schema and runs the shared state machines,
  actions and hooks; subtypes (`extends = RequestResource`) own their CRUD.
  One id space, references to any kind, base grants cover reads and writes
  need the subtype's. Joined tables in SQL (`extension_table`, `map_family`),
  `anyOf` in OpenAPI, a GraphQL interface implemented by each subtype.
- View resources: read-only resources computed by a query, with the usual
  filters, sorting, permissions, tenancy, `expand`, docs, GraphQL and MCP
  tools. `Aggregate` views (`Count`, `Sum`, `Min`, `Max`, `Avg`, with `where`)
  run in every storage adapter (one `GROUP BY` in SQL, `map_views`); custom
  views map any `select` (`map_view`). The example gains `customer_balances`.
- API keys (`registry.register_api_keys()`, `ApiKeyAuthenticator`): `rek_<id>_<secret>`
  sent as a bearer token or `X-API-Key`, the secret hashed and shown once
  (also kept out of idempotency records), roles limited to the creator's
  permissions, the creator's tenant, expiry, the `revoke` transition,
  `last_used_at`; other tokens fall back to OIDC. The example app accepts them.
- Rate limits (`RateLimiter`): per caller, per anonymous address, failed
  authentications per address (checked before the credentials), and per
  engine action in the manager for every adapter; sliding window, 429 with
  `Retry-After` and `RateLimit-*` headers (RE-E710). Counters in memory or in
  Redis/Valkey (`resource-engine-redis`, atomic, fail open). `RateLimiter` is
  a port: `SlidingWindowLimiter` is the built-in implementation.
- Caching: a `Cache` port with `InMemoryCache` and a signed `RedisCache`; the
  manager caches records read by id for resources with `cache_ttl`, permission
  checks still run per caller, writes through the manager forget them after
  commit. HTTP: ETag on every JSON `GET` (version or body hash), 304 on
  `If-None-Match`, `Cache-Control` (default `private, no-cache`, per resource
  `cache_control`) and `Vary` on the credentials.
- Opaque access tokens: `IntrospectionAuthenticator` (RFC 7662) with a short
  cache of active answers (the `Cache` port), `keycloak_introspection` and
  `okta_introspection` presets (Auth0 has no introspection: its API tokens are
  JWTs), `TokenRouter` for JWTs and opaque tokens side by side; revocation
  seen within `cache_ttl`.
- Documentation site (`resource-engine docs site`): the functional docs as
  static HTML with navigation, search, Mermaid state diagrams, the permission
  matrix and the published events; escaped docstrings, dark mode, phones and
  print; Mermaid from a CDN or self-hosted.
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
- Sender-constrained tokens (`SenderConstrained`): DPoP proofs (RFC 9449:
  key thumbprint, method, URL, age, token hash, one use through an atomic
  `replay_cache`) and certificate-bound tokens (RFC 8705, the client
  certificate forwarded by the proxy); no downgrade to bearer; `Credentials`
  and `RequestAuthenticator` in the core, `bearer_actor(client_certificate=,
  public_url=)`, `WWW-Authenticate: DPoP` challenges.
- SAML sign-in through an identity broker (`docs/saml.md`): the broker
  federates the SAML IdP and issues the OIDC tokens the API verifies. The
  example stack gains a `partner` realm as Initech's SAML IdP (signed
  assertions, `Role` attribute mapped to realm roles, users joined to the
  Initech organization), tested end to end through the browser flow,
  including a tampered assertion.
- Complete scaffolding: `resource-engine scaffold hook|event|slot|reference|policy`
  (and the development MCP server's tools) write each part and declare it on
  the resource class with comment-preserving edits; `scaffold action` now
  declares the action too. References add the id field to the entity,
  schemas and table; seed policies take the resource's actions from the
  registered resource.
- Email and templates: `EmailRequested` recorded in the transaction and sent
  from the outbox after the commit by `Mailer.router()` (inbox, retries,
  permanent refusals settled, stable `Message-ID`); `EmailSender` and
  `TemplateRenderer` ports; `resource-engine-smtp`, SES in
  `resource-engine-aws[ses]`, `resource-engine-jinja` (locale fallback,
  sandbox, autoescaping); `EventRouter.include`; `check` RE-C007; Mailpit in
  the compose stack; the example emails the customer when an invoice is
  approved.
- Documentation of emails and hooks: each resource lists the emails it sends
  (trigger, recipients as written) and what runs in each write operation, in
  lifecycle order, with a small diagram; an emails page previews every
  template set per locale with placeholders, HTML in a sandboxed frame.
- Translations (`docs/i18n.md`): gettext catalogues read with the standard
  library (`Translations`, the locale in a context variable, `Accept-Language`
  negotiation and the token's `locale` claim, `Content-Language`); a `title`
  per error code in the request's language (engine catalogues in Catalan and
  Spanish); Jinja `{% trans %}` and `_()`; `resource-engine i18n extract |
  update | compile | status | export`; the functional docs and the site in
  every configured language, with a switcher (generator texts shipped in
  English, Catalan and Spanish). The example runs in Catalan, English and
  Spanish.
- Documentation audiences: `--audience technical|functional` for `docs site`
  and `docs generate` (`docs_audience` in pyproject). Technical adds class
  paths, endpoints, schemas, the HTTP API page from the app's OpenAPI, the
  events' payload schemas and the GraphQL schema, with `openapi.json`,
  `asyncapi.json` and `schema.graphql`; functional keeps the plain-words
  pages only. The site workflow takes the audience as an input (or the
  `DOCS_AUDIENCE` variable).
- Explorer: `docs site --console` adds a try-it panel to every resource page
  (operations from the app's OpenAPI with their parameters and a starting
  JSON body, the visitor's token, reads only unless "Allow changes", the
  allowed transitions of a record, why a call was refused) and a "what can I
  do?" panel; `mount_docs(app, "site")` serves it at `/explorer/` with a
  Content-Security-Policy. The example mounts it with `DOCS_SITE`.
- Audit shipping and retention rules: `OutboxAuditSink` queues each entry in
  the change's transaction and `AuditForwarder` ships it from the worker
  (exactly once, retried, dead-lettered when refused) through `AuditExporter`s:
  syslog RFC 5424 with CEF or JSON, HTTP (JSON lines, Splunk HEC, Elastic
  bulk), OpenTelemetry log records. `Retention` rules per resource and outcome
  (first match, `None` keeps forever); with a sealer, a batch goes when every
  entry in it is past its own rule.
- Policy-as-code support: `cedar_schema(registry)` and `resource-engine authz
  cedar-schema --check policies/` validate Cedar policies against the real
  resources, actions and attributes (and `CedarEngine` sends only declared
  attributes); `resource-engine authz opa-bundle` builds a reproducible OPA
  bundle with the Rego files and the catalogue as data; OpenFGA conditions
  (`conditions=True`) get the input and the record as CEL parameters, deciding
  at step 5b.
- Table export and generated documents: `GET /<resource>/export?format=csv|xlsx`
  (`export_table`) with the list's filters, sorting, row filter, tenant and
  hidden fields, up to 10 000 rows; `DocumentGenerator` renders `documents/`
  templates per locale to HTML, Markdown, text or PDF (`PdfConverter`,
  `GotenbergPdfConverter` in `resource-engine-http`, Gotenberg in compose) and
  attaches them to a slot inside the operation.
- Email providers and bounces: `PostmarkEmailSender`, `SendGridEmailSender`,
  `GraphEmailSender` (`resource-engine-http`); `EmailEvent` from webhooks
  (`parse_postmark`, `parse_sendgrid`, `parse_ses`), `mount_email_events` with
  a shared token, `EmailEventHandler` suppressing hard bounces, complaints and
  unsubscribes in a `SuppressionList` (in memory or `SqlAlchemySuppressionList`),
  and the mailer leaving suppressed recipients out.
- Cloud adapters: `AzureBlobStore` (block uploads committed at the end, SAS
  URLs; Azurite) and `GcsBlobStore` (resumable uploads cancelled on failure,
  V4 signed URLs; fake-gcs-server) pass `BlobStoreContract`;
  `EventBridgePublisher` (`PutEvents`, `detail-type` = event type) and
  `EventGridPublisher` (CloudEvents batches under 1 MB, topic key or Entra).
- Data-lake views (`docs/query-engines.md`): `QueryEngineStore` serves view
  resources from a `QueryEngine`, compiling filters, the row filter, tenancy,
  sorting and paging to parameterised SQL per dialect; `TrinoEngine` and
  `BigQueryEngine` (`resource-engine-http`), `AthenaEngine` (`[athena]`), each
  with a scan or billing limit and a timeout; Trino in compose.
- Translatable data: `Resource.translatable` fields stored as `{locale: text}`,
  read in the request's language with fallbacks (base language,
  `default_locale`, any), written per language or all at once
  (`<field>_translations`), filtered and sorted in that language in memory and
  in SQL (`COALESCE` over JSON paths).
- Composite stores (`docs/composite.md`): `CompositeStore` routes each
  resource to its store, keeps the outbox, inbox and audit in the primary one,
  and wraps non-transactional repositories so their writes record a
  compensation (delete, restore, re-create, or your own), run in reverse when
  the operation or the primary commit fails; failing compensations are
  reported and the others still run.
- NoSQL repositories (`docs/nosql.md`): capability flags in the repository
  port (`filterable`, `sortable`, `max_offset`, `transaction_scope`,
  `max_writes`) checked by the manager (`QueryNotSupported`) and reported by
  `capability_problems`; `resource_engine.nosql` buffers writes and applies
  them at commit in one store transaction (`TransactionTooLarge`, RE-E012);
  `DynamoDbStore`, `FirestoreStore` and `CosmosStore` pass the repository,
  condition and new version contracts against their emulators.
- SAP (`docs/sap.md`): `ODataRepository` over v2 and v4 entity sets on top
  of `RestRepository` (`$filter` from filters and row filters, `$orderby`,
  paging and counts, v2 dates and exact decimals, CSRF tokens, ETags as
  `If-Match`), and `DestinationService` / `destination_client` (bindings from
  Cloud Foundry or Kyma, `authTokens` headers renewed on expiry and 401,
  principal propagation; on-premise destinations refused).
- Versioning (`docs/versioning.md`): API versions under their prefix
  (`mount_resources(versions=...)`, `Resource.api_versions` converting input
  and output); event `schema_version` with older versions published side by
  side and consumers picking one; `Deprecation` notices as response headers
  (RFC 9745, 8594), in OpenAPI, AsyncAPI and GraphQL; contract checks per
  event version and for OpenAPI (`docs openapi --check`), letting a version go
  after its sunset.
- Reference document processors (`docs/documents.md#reference-processors`):
  `ClamAvScanner` (`resource-engine-clamav`, clamd `INSTREAM`, retried while
  clamd is down), `ContentTypeCheck` (magic bytes against the declared type,
  executables refused), `TextExtraction` with `BuiltinTextExtractor` (text,
  CSV, JSON, XML, `.docx`) and `TikaTextExtractor` (`resource-engine-http`);
  ClamAV and Tika in compose.
- Background exports and printouts (`docs/exports.md#background-exports`,
  `docs/documents.md#printouts`): `ExportJobs` (`manager.exports`) checks the
  request, writes CSV or XLSX from a worker job as the requester and keeps
  job and file in the blob store; `POST /<resource>/exports`, `GET
  /exports/{id}` and `/content`; `exportInvoices` and `export(id)` in
  GraphQL, `<resource>_export` and `_export_status` in MCP; download links in
  the explorer's console; `Resource.printouts` rendered on request at `GET
  /<resource>/{id}/printouts/<name>`; `EmailRequested(documents=[...])`
  attaching stored documents.
- Explorer, part 1 (`docs/explorer.md`): `mount_explorer` serves a standalone
  page like FastAPI's `/docs`, built from the running app (Operations and
  Events tabs, en/ca/es), whose console and texts the docs site now reuses;
  committed events live over server-sent events through `LiveEvents` (shared
  with the GraphQL subscriptions), with per-consumer deliveries from an
  `ActivityLog` (`ActivityMessageObserver`, `SqlAlchemyActivityLog`, purged by
  `MessagingJanitor`); engine actions (`registry.register_actions`,
  `register_explorer`) with `exact` actions no wildcard grants; and
  `manager.audit_event` for audit entries outside operations.
- Documentation and explorer apart: the generated documentation (`docs
  generate`, `docs site`) is functional only, with `openapi.json`,
  `asyncapi.json` and `schema.graphql` written next to it and linked from the
  home page; the technical audience and its API pages, and the site's try-it
  console (`--console`, `--api-url`), are gone in favour of the explorer;
  `mount_docs` serves at `/documentation/`, and the explorer's endpoints stay
  out of the app's OpenAPI document.
## Next

Ordered by priority. The MVP of the
[proposal](proposal-v3.md#12-abast-proposat-per-a-una-primera-versió-mvp) is complete;
what follows builds on it.

### Soon

- Explorer, part 2: a Jobs tab with the worker's jobs, their runs and "run
  now" through the worker, and a record's pending timed transitions.
- Explorer, part 3: a dry run of publishing an event, validated against its
  schema and the app's consumers.
- Explorer, part 4: publishing an event by hand through the outbox, tagged
  and audited.

### Later

