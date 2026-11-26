# Adapter catalogue

Adapters implement the engine's [ports](architecture.md#ports). Official adapters
live in this repository under `adapters/` while the ports are still changing; they
will move to a dedicated repository once the core reaches a stable 0.x API.
Third-party adapters are welcome: pass the [contract tests](#contract-tests) and
open a PR adding a row below.

Status: **experimental** = API may change · **planned** = not started.

## Naming and packaging

Packages are named after what they talk to:

| Kind | Name | Examples |
|---|---|---|
| A provider's own services | `resource-engine-<provider>` | `aws` (SQS, SNS), `azure` (Service Bus), `gcp` (Pub/Sub), `sap` (Event Mesh) |
| A protocol many vendors implement | `resource-engine-<protocol>` | `s3` (AWS S3, Garage, MinIO, Ceph, R2), `oidc` (Keycloak, Cognito, Entra ID, Google), `http` (REST, SOAP), `rabbitmq` (AMQP), `redis` (Redis, Valkey) |
| A product with its own engine | `resource-engine-<product>` | `opa`, `cedar`, `openfga`, `sqlalchemy`, `fastapi` |

Inside a provider package, each service is an extra and a module:
`resource-engine-aws[sqs]` installs only what SQS needs, from
`resource_engine_aws.sqs`. New services of a provider become new extras, not
new packages. EventBridge goes in `aws[eventbridge]`, Azure Blob Storage in
`azure[blob]`, Google Cloud Storage in `gcp[storage]`.

A service gets a package of its own only when it needs a dependency the
provider's other services don't, or when it is released or maintained apart.
A protocol package never carries a provider's name: S3 storage works the same
on MinIO and R2, so it lives in `resource-engine-s3`, not in `aws`.

## Messaging — `MessagePublisher`

| Adapter | Package / extra | Class | FIFO / ordering | Tested against | Status |
|---|---|---|---|---|---|
| Amazon SQS | `resource-engine-aws[sqs]` | `SqsMessagePublisher` | FIFO queues: group = envelope key, dedup = envelope id | Official AWS Emulator | experimental |
| Amazon SNS | `resource-engine-aws[sns]` | `SnsMessagePublisher` | FIFO topics: same as SQS | Official AWS Emulator (SNS→SQS) | experimental |
| Azure Service Bus | `resource-engine-azure[servicebus]` | `ServiceBusMessagePublisher` | `sessions=True`: session = envelope key | Unit tests only (see below) | experimental |
| Google Pub/Sub | `resource-engine-gcp[pubsub]` | `PubSubMessagePublisher` | `ordering=True`: ordering key = envelope key | Official Pub/Sub emulator | experimental |
| RabbitMQ | `resource-engine-rabbitmq` | `RabbitMqMessagePublisher` | publisher confirms; routing key `<resource>.<type>` | RabbitMQ 4 (compose) | experimental |
| Amazon EventBridge | `resource-engine-aws[eventbridge]` | – | – | – | planned |
| Azure Event Grid | `resource-engine-azure[eventgrid]` | – | – | – | planned |
| SAP Event Mesh | `resource-engine-sap[event-mesh]` | `EventMeshMessagePublisher` | Topic or queue; `x-qos: 1` by default | Recorded HTTP exchanges | experimental |
| SAP advanced event mesh (Solace) | `resource-engine-sap[advanced-event-mesh]` | `AdvancedEventMeshPublisher` | `JMSXGroupID` = envelope key (partitioned queues) | Recorded HTTP exchanges | experimental |
| Webhook (any HTTP endpoint) | `resource-engine-http` | `WebhookMessagePublisher` | – (`Idempotency-Key` = envelope id) | Fake endpoint | experimental |

Cloud publishers send the envelope JSON as the body and the envelope attributes
(`event_type`, `event_id`, `resource`, `correlation_id`, `schema_version`) as
broker attributes, so subscriptions can filter by event type or resource. They
split batches to respect broker limits and raise `PublishFailed` with only the
ids that were not accepted, which the outbox relay retries.

HTTP-based publishers (webhook, SAP) send one request per message, up to
`max_concurrency` at once, and can use `format="cloudevents"` (structured-mode
CloudEvents 1.0, see `resource_engine.messaging.to_cloudevent`). Event Mesh
defaults to CloudEvents because SAP applications emit and expect it.

### SAP

```python
from resource_engine_sap import EventMeshMessagePublisher, event_mesh_client, event_topic

client = event_mesh_client(service_key)  # dict from the BTP service key; OAuth2 handled
publisher = EventMeshMessagePublisher(
    client, topic=event_topic(service_key["namespace"]), type_prefix="acme.billing."
)
# topic: <namespace>/<resource>/<EventType>

from resource_engine_sap import AdvancedEventMeshPublisher
from resource_engine_sap.advanced_event_mesh import event_topic as aem_topic

aem = httpx.AsyncClient(base_url="https://<broker>:9443", auth=httpx.BasicAuth(user, pwd))
publisher = AdvancedEventMeshPublisher(aem, topic=aem_topic("acme/billing"))
# topic: acme/billing/<resource>/<EventType>/<entity id>; subscribe with acme/billing/invoices/>
```

Neither service has a public emulator, so the tests check the exact HTTP requests
(paths, `x-qos`, Solace headers, OAuth token caching). Before relying on them in
production, run the publishers once against a trial or development instance.

## Consumers — `MessageSource` and push endpoints

| Broker | Package / extra | Pull source | Push | Dead-letter | Tested against | Status |
|---|---|---|---|---|---|---|
| Amazon SQS (and SNS→SQS) | `resource-engine-aws[sqs]` | `SqsMessageSource` | – | Queue redrive policy (`maxReceiveCount`) | Official AWS Emulator | experimental |
| Google Pub/Sub | `resource-engine-gcp[pubsub]` | `PubSubMessageSource` | `decode_pubsub_push` | Subscription dead-letter policy | Official Pub/Sub emulator | experimental |
| RabbitMQ (quorum queues) | `resource-engine-rabbitmq` | `RabbitMqMessageSource` | – | Queue DLX via `declare_topology` (explicit `dead_letter`) | RabbitMQ 4 (compose) | experimental |
| Azure Service Bus | `resource-engine-azure[servicebus]` | `ServiceBusMessageSource` | – | `dead_letter_message` (explicit) | Fake receiver | experimental |
| SAP Event Mesh | `resource-engine-sap[event-mesh]` | `EventMeshMessageSource` | webhook → `build_event_receiver` | Queue dead-message queue | Fake REST queue | experimental |
| SAP advanced event mesh | `resource-engine-sap` | – | REST delivery point → `build_event_receiver` | Broker DMQ | – | experimental |
| Any HTTP sender | `resource-engine-fastapi` | – | `build_event_receiver` | Status code (`dead_letter_status`) | ASGI tests | experimental |

Every source passes `MessageSourceContract`. Broker notes:

- **SQS**: `retry` changes visibility to the delay (up to 12 h); SNS notifications
  without raw delivery are unwrapped.
- **Pub/Sub**: `retry` sets the ack deadline (max 600 s); delivery counts appear
  only with a dead-letter policy on the subscription.
- **Service Bus**: `retry` abandons (immediate redelivery, delay ignored).
- **RabbitMQ**: `retry` nacks with requeue (immediate, delay ignored); delivery
  counts from `x-acquired-count` (RabbitMQ 4) on quorum queues; open the channel
  with `publisher_confirms=True, on_return_raises=True`.
- **Event Mesh (REST)**: one message per call, no long polling (`poll_interval`),
  no negative ack and no delivery count; unacknowledged messages come back after
  the broker's timeout. Webhook-consumed messages cannot use the ack API, so push
  delivery goes through `build_event_receiver`.

Keep the broker's own limit (`maxReceiveCount`, `MaxDeliveryCount`,
`maxDeliveryAttempts`) above `RetryPolicy.max_deliveries`, so the consumer decides
first and the broker is the safety net.

## Persistence — `Repository`, `UnitOfWork`, `OutboxStore`

| Adapter | Package | Notes | Status |
|---|---|---|---|
| SQLAlchemy 2 (async) | `resource-engine-sqlalchemy` | Covers RDS/Aurora, Azure SQL / Database for PostgreSQL, Cloud SQL / AlloyDB. Outbox with `SKIP LOCKED`; tables for documents, permissions and the audit trail; row filters and version compare-and-set in SQL. | experimental |
| In-memory | `resource-engine` (`resource_engine.testing`) | Tests and prototypes. | experimental |
| REST API | `resource-engine-http` | `RestRepository` / `rest_unit_of_work_factory`. Not transactional (see below). | experimental |
| SOAP service | `resource-engine-http[soap]` | `SoapRepository` over `SoapService` (zeep). Not transactional. | experimental |
| SAP OData (S/4HANA APIs) | `resource-engine-sap[odata]` | On top of `RestRepository`. | planned |
| DynamoDB, Cosmos DB, Firestore | – | Deferred: need capability flags in the repository port (transactions scope, total counts, index-bound filters). | planned |

### Remote repositories (REST, SOAP)

Use them when a resource's data lives in another system: the engine still runs
validation, hooks, the state machine and events, and the remote API stores the data.

```python
from resource_engine_http import OAuth2ClientCredentials, RestEndpoint, rest_unit_of_work_factory

client = httpx.AsyncClient(
    base_url="https://crm.example/api",
    auth=OAuth2ClientCredentials(token_url, client_id, client_secret),
)
uow = rest_unit_of_work_factory(
    client, {"customers": RestEndpoint.for_dataclass(Customer, "/customers")}
)
manager = ResourceManager(registry, uow)
```

The defaults (`offset`/`limit`/`order_by`/filters as query parameters,
`{"items", "total"}` or a bare list with `X-Total-Count`, `PATCH` for updates)
match `resource-engine-fastapi`, so one engine can serve another's resources.
Override `list_params`, `parse_page`, `item_path` and `update_method` for other APIs.

For SOAP, `SoapService.from_wsdl(...)` gives `await soap.call("Operation", **args)`
returning plain dicts; register it as a service for hooks and actions, or map CRUD
onto operations with `SoapOperations` and `SoapRepository`.

**Remote writes are not transactional.** The request is sent when the repository is
called, so a failure later in the lifecycle (`after_<op>` hooks, commit) does not
undo it. Validate before the mutation (`validate`, `before_<op>`), prefer
idempotent APIs, and use `AfterCommitDelivery` (`OutboxDelivery` needs a local
transaction and fails with RE-E403). Upstream errors raise `RemoteCallFailed`
(RE-E501, HTTP 502). The repository contract runs against both adapters except for
the two rollback tests.

## Storage — `BlobStore`

| Store | Package | Class | Presigned URLs | Tested against | Status |
|---|---|---|---|---|---|
| S3 API (AWS S3, Garage, MinIO, Ceph RGW, Cloudflare R2) | `resource-engine-s3` | `S3BlobStore` | GET and PUT | Garage (compose) | experimental |
| In-memory | `resource-engine` (`resource_engine.testing`) | `InMemoryBlobStore` | fake (`memory://`) | contract | experimental |
| Azure Blob Storage, Google Cloud Storage | `resource-engine-azure[blob]`, `resource-engine-gcp[storage]` | – | – | – | planned |

`BlobStoreContract` checks round trips, sha256, streaming, `max_bytes` aborts
without leftovers, deletes, listing and presigned upload/download. Use
`s3_client_options(endpoint, path_style=True)` for Garage and MinIO. See
[documents](documents.md).

## Identity — `Authenticator`

| Provider | Package | Preset | Tested against | Status |
|---|---|---|---|---|
| Keycloak | `resource-engine-oidc` | `keycloak` | Keycloak 26 (compose) | experimental |
| Amazon Cognito | `resource-engine-oidc` | `cognito` | signed test tokens | experimental |
| Microsoft Entra ID | `resource-engine-oidc` | `entra_id` | signed test tokens | experimental |
| Google Identity Platform / Firebase Auth | `resource-engine-oidc` | `google_identity_platform` | signed test tokens | experimental |
| Google-signed ID tokens (Cloud Run, Pub/Sub push) | `resource-engine-oidc` | `google_id_token` | signed test tokens | experimental |
| Any OIDC provider | `resource-engine-oidc` | `OidcAuthenticator` | – | experimental |

See [identity](identity.md).

## Locks — `LockProvider`

| Backend | Package | Class | Tested against | Status |
|---|---|---|---|---|
| SQL lease table | `resource-engine-sqlalchemy` | `SqlAlchemyLocks` | SQLite (contract) | experimental |
| In-memory | `resource-engine` (`resource_engine.testing`) | `InMemoryLocks` | contract | experimental |

See [background jobs](jobs.md#one-worker-at-a-time).

## Rate limits — `RateLimitStore`

| Backend | Package | Class | Tested against | Status |
|---|---|---|---|---|
| Redis / Valkey | `resource-engine-redis` | `RedisRateLimitStore` | Valkey 8 (compose) | experimental |
| In-memory | `resource-engine` (`resource_engine.ratelimit`) | `InMemoryRateLimitStore` | unit tests | experimental |

See [rate limits](rate-limits.md).

## Cache — `Cache`

| Backend | Package | Class | Tested against | Status |
|---|---|---|---|---|
| Redis / Valkey | `resource-engine-redis` | `RedisCache` (signed entries) | Valkey 8 (compose) | experimental |
| In-memory | `resource-engine` (`resource_engine.cache`) | `InMemoryCache` | unit tests | experimental |

See [caching](caching.md).

## Tracing — `Tracer`

| Backend | Package | Class | Tested against | Status |
|---|---|---|---|---|
| OpenTelemetry | `resource-engine-opentelemetry` | `OtelTracer` | OpenTelemetry SDK in-memory exporter | experimental |
| In-memory | `resource-engine` (`resource_engine.testing`) | `RecordingTracer` | – | experimental |

See [observability](observability.md).

## Metrics — `MessageObserver`

| Backend | Package | Class | Tested against | Status |
|---|---|---|---|---|
| OpenTelemetry | `resource-engine-opentelemetry` | `ConsumerMetrics` | OpenTelemetry SDK in-memory reader | experimental |
| Logging | `resource-engine` | `LoggingObserver` | – | experimental |

See [messaging](messaging.md#metrics).

## Authorization — `PolicyEngine`

| Engine | Package | Class | Row filters | Tested against | Status |
|---|---|---|---|---|---|
| Open Policy Agent | `resource-engine-opa` | `OpaEngine` | Compile API (UCAST) | OPA 1.22.4 (compose) | experimental |
| Cedar | `resource-engine-cedar` | `CedarEngine` | Partial evaluation (cedarpy) | in process | experimental |
| OpenFGA | `resource-engine-openfga` | `OpenFgaEngine`, `TupleSync` | `list_objects` | OpenFGA 1.22.0 (compose) | experimental |
| In-memory | `resource-engine` (`resource_engine.testing`) | `ConditionEngine` | Partial evaluation | contract | experimental |

Engines run behind `EngineAuthorizer`. `AuthorizerContract` checks them. The
built-in `PolicyAuthorizer` needs no engine. See
[external policy engines](authorizers.md).

## HTTP runtimes

FastAPI apps run unchanged on Cloud Run, App Service/Container Apps and ECS/App
Runner. Serverless runtimes (Lambda, Azure Functions) need an ASGI bridge; no
engine-specific adapter is planned for them.

## Choosing the delivery mode

```python
from resource_engine.messaging import OutboxDelivery, OutboxRelay, TransportEventPublisher

# Best effort, simplest: publish right after commit.
manager = ResourceManager(registry, uow, event_publisher=TransportEventPublisher(sqs_publisher))

# Guaranteed (at-least-once): store events with the transaction, relay them later.
manager = ResourceManager(registry, uow, event_delivery=OutboxDelivery())
relay = OutboxRelay(uow, sqs_publisher)  # run_once() from a job, or run_forever()
```

See [messaging.md](messaging.md).

## Contract tests

`resource_engine.testing.contracts` holds the behaviour every adapter must keep.
Subclass the contract and provide its fixtures:

| Contract | Fixtures | Checks |
|---|---|---|
| `UnitOfWorkContract` | `contract_uow_factory` | commit visibility, rollback on exit and on exception |
| `RepositoryContract` | `contract_uow_factory` (serving `ContractItem` as `contract_items`) | ids, get/update/delete, filters, ordering, paging, totals |
| `OutboxContract` | `contract_uow_factory` with `outbox()` | ordering, rollback discards, published/failed bookkeeping |
| `MessagePublisherContract` | `message_publisher`, `receive(n)` | lossless round trip, batches above broker limits, empty publish |
| `MessageSourceContract` | `message_source`, `send(envelopes)` | decoding, ack removes, retry redelivers (count + 1), bounded empty waits |
| `InboxContract` | `contract_uow_factory` with `inbox()` | marks per consumer, committed only |
| `BlobStoreContract` | `blob_store`, optional `presigned_client` | round trip + sha256, streaming, `max_bytes` without leftovers, delete, list, presigned PUT/GET |

```python
class TestMyPublisher(MessagePublisherContract):
    @pytest.fixture
    def message_publisher(self): ...
    @pytest.fixture
    def receive(self): ...
```

## Emulators

`compose.emulators.yaml` starts the official cloud emulators and open-source local
services used by the tests (and handy for local development):

```bash
docker compose -f compose.emulators.yaml up -d --build
uv run pytest -m emulator
```

| Service | Image | UI / port | Notes |
|---|---|---|---|
| Garage (S3) | `dxflrs/garage:v2.4.1` | no official UI; S3 on :3910 | Run `uv run python docker/garage/init.py` once (layout, bucket `resource-engine`, dev key); the S3 tests run it too. AGPL, used only as a dev tool. |
| RabbitMQ | `rabbitmq:4-management` | http://127.0.0.1:15672 (guest/guest) | AMQP on :5672. |
| Valkey | `valkey/valkey:8-alpine` | no UI; Redis protocol on :6380 | Rate-limit counters (`resource-engine-redis`). |
| Keycloak | `quay.io/keycloak/keycloak:26.4` | http://127.0.0.1:8180 (admin/admin) | Realm imported from `docker/keycloak/`. |
| OPA | `openpolicyagent/opa:1.22.4` | REST on :8181 | Serves `docker/opa/*.rego` and reloads on change. |
| OpenFGA | `openfga/openfga:v1.22.0` | playground http://127.0.0.1:3000; API on :8080 | In-memory datastore: tests create a store per test. |

| Provider | Emulator | Notes |
|---|---|---|
| PostgreSQL | `postgres:17-alpine` | `127.0.0.1:5439`, user, password and database `resource_engine` | Migration tests (`examples/invoices/migrations`) and the SQL contracts, `SKIP LOCKED` and leases. Each test creates a database of its own. |
| AWS | [AWS Emulator](https://docs.aws.amazon.com/emulator/latest/userguide/) | No prebuilt image: built from the official recipe in `docker/aws-emulator/`. Runs with `--auth off --telemetry off`; the self-signed CA is written to `.emulators/aws/9297/ca.pem`, readable only by uid 1000 (the container user): if your uid differs, run `sudo chmod -R a+rX .emulators/aws` after start-up, or the tests skip with a `PermissionError`. It enforces SQS queue policies for SNS delivery, as AWS does. |
| Google Cloud | Pub/Sub emulator (`google-cloud-cli:emulators`) | Set `PUBSUB_EMULATOR_HOST`. |
| SAP | – | Event Mesh and advanced event mesh have no public emulator; tests check the HTTP requests. A local Solace PubSub+ Standard broker accepts the same REST calls as advanced event mesh, but it has its own licence terms, so it is not wired. |
| Azure | Service Bus emulator | Not wired: it requires accepting Microsoft's EULAs for the emulator and SQL Server. Run it yourself if you accept them. |

Tests are marked `emulator("<name>")` (`aws`, `pubsub`, `garage`, `rabbitmq`,
`keycloak`, `opa`, `openfga`, `postgres`) and each group skips automatically when its service is not reachable.
