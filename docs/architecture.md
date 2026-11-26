# Architecture

## Layers

| Layer | Where | May import |
|---|---|---|
| Domain | `resource_engine.domain`, each module's `domain/` | stdlib, `resource_engine.domain`, `python-statemachine` |
| Application | `resource_engine.application`, `resource_engine.ports`, each module's `application/` | domain, pydantic |
| Infrastructure / adapters | `resource_engine_sqlalchemy`, `resource_engine_fastapi`, `resource_engine_graphql`, `resource_engine_mcp`, each module's `infrastructure/` | anything |

`resource-engine check` enforces the domain rule (RE-C001), checks that hooks
match their lifecycle point (RE-C002, RE-C003; see [lifecycle](lifecycle.md#external-effects)),
finds operations nobody but `admin` can perform (RE-C004; see
[permissions](permissions.md#checking-the-gates)) and reports declarations the engine
refuses (RE-C005), and flags domain events recorded but not declared on the resource
(RE-C006).

Domain entities are plain classes. SQLAlchemy maps them with **imperative mapping**
from the infrastructure layer (`examples/invoices/infrastructure/persistence.py`),
so the same class is used by the domain, the in-memory adapter and SQLAlchemy.

## Ports

- `Repository[E]`: `get`, `list(ListQuery) -> Page`, `add`, `update`, `delete`; optional `supports_conditions` (row filters) and `supports_versions` (`expected_version` compare-and-set, see [concurrency](concurrency.md)).
- `UnitOfWork`: async context manager handing out repositories; `commit`/`rollback`.
- `EventPublisher`: `publish(events)`, called only after commit.
- `MessagePublisher`: `publish(envelopes)` to a broker (cloud adapters implement it).
- `OutboxStore` / `SupportsOutbox`: transactional outbox reached through the unit of work; `purge_published` for retention.
- `MessageSource` (+ `SupportsDeadLetter`): pull side of a broker (`receive`, `ack`, `retry`).
- `InboxStore` / `SupportsInbox`: processed message ids for idempotent consumers; `purge` for retention (`MessagingJanitor`).
- `ErrorReporter`: receives every unexpected failure once, with its context (`LoggingReporter` by default, `SentryReporter` in `resource-engine-sentry`; [observability](observability.md#error-reporting)).
- `RateLimiter`: decides whether a caller may make one more request (`SlidingWindowLimiter` over a `RateLimitStore`: `InMemoryRateLimitStore`, `RedisRateLimitStore` in `resource-engine-redis`; [rate limits](rate-limits.md)).
- `Cache`: values with a TTL, for the manager's record cache (`InMemoryCache`, `RedisCache`; [caching](caching.md)).
- `OperationObserver`: metrics hook called after every manager operation (`OperationMetrics` in `resource-engine-opentelemetry`; [observability](observability.md#metrics)).
- `JobObserver`: called after every `JobRunner` run (`JobMetrics` in `resource-engine-opentelemetry`).
- `MessageObserver`: metrics hook called after every processed message (`LoggingObserver` in the core, `ConsumerMetrics` in `resource-engine-opentelemetry`).
- `BlobStore` (+ `SupportsPresignedUrls`): document contents (`put`, `open`, `head`, `delete`, `list`).
- `Authenticator` → `Principal`: who is calling (identity providers implement it).
- `Authorizer`: may the principal perform an action (on a record); `scope` gives the row filter. `PolicyAuthorizer` is the built-in one.
- `PolicyEngine`: `evaluate(AuthzRequest)` → allowed, denied or a residual condition; OPA, Cedar and OpenFGA adapters implement it, and `EngineAuthorizer` turns it into an `Authorizer` ([authorizers](authorizers.md)).
- `Tracer`: spans around operations, outbox publishing, messages and jobs, and the trace context put on envelopes (`NoopTracer` by default, `OtelTracer` in `resource-engine-opentelemetry`; [observability](observability.md)).
- `LockProvider`: named leases so a `single` job runs on one worker at a time (`SqlAlchemyLocks`, `InMemoryLocks`; [jobs](jobs.md)).
- `AuditSink`: where audit entries go (`RepositoryAuditSink` writes the `audit_log` resource in the operation's transaction).

## Infrastructure services and the lifecycle

| Lifecycle step | Messaging (SQS, RabbitMQ, Event Mesh…) | Documents (S3, Garage…) | Identity (Keycloak, Cognito, Entra, Google) |
|---|---|---|---|
| Entry adapter | push endpoint → `MessageProcessor` | upload/download routes | bearer token → `Principal` (`actor`) |
| 2 / 5b Authorization | – | attach / detach / download per slot | `Principal` roles → system roles and policies |
| 3 Validate | – | slot, content type, declared size | – |
| 4–8 Transaction | outbox rows (`OutboxDelivery`) | `Document` rows; blobs as staged effects | – |
| 9 Commit | outbox committed | documents visible | – |
| 10 After commit | direct publishing / relay | purge detached blobs; `Document*` events | – |
| Outside requests | consumers (`MessageConsumer`, inbox) | pipeline, `DocumentJanitor` | outbound `OAuth2ClientCredentials` |

`resource_engine.testing` provides `InMemoryStore`, `InMemoryUnitOfWork`,
`InMemoryQueue` (publisher and source), `InMemoryBlobStore`, `RecordingEventPublisher`
and the adapter contracts.

## Repository strategy (monorepo now, multirepo later)

The workspace is organised so that splitting is a mechanical move:

1. Every package under `packages/` has its own `pyproject.toml`, version, README
   and tests, and builds independently (`uv build --package <name>`).
2. Packages reference each other only by distribution name
   (`dependencies = ["resource-engine"]`); `[tool.uv.sources]` at the root resolves
   them to the workspace. After a split, drop the source entry and depend on the
   published version range.
3. Top-level import names are distinct (`resource_engine_fastapi`, ...) rather than
   a namespace package, so packages never need to be installed together.
4. Cross-package tests live in `examples/`, which depends on all packages, and can
   become an integration repository.

Adapter packages are named after a provider, a protocol or a product, with one
extra per service in provider packages; see
[naming and packaging](adapters.md#naming-and-packaging).

Likely split order: cloud adapters (`adapters/` → an adapters repository, once
the ports are stable; the contract suite makes them verifiable from outside),
then devtools (fastest release cadence, AI skill).
