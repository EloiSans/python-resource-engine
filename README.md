# Resource Engine

Declarative resources for Python business backends: CRUD, lifecycle hooks, state
machines and business actions on a hexagonal architecture, with functional
documentation and AI-agent context generated from the same definitions.

> Status: early development (`0.1.0.dev0`). The public API may change.
> Permissions: system roles in code, ABAC policies managed from front ends, with
> operation, row and field rules (see [docs/permissions.md](docs/permissions.md)).

## Packages

This repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/).
Each package is published and versioned on its own, so any of them can later be
moved to its own repository without code changes.

| Package | Import | Depends on | Purpose |
|---|---|---|---|
| `resource-engine` | `resource_engine` | pydantic, python-statemachine | Core: `Resource`, registry, hooks, `ResourceManager`, documents, messaging, ports, in-memory adapters |
| `resource-engine-fastapi` | `resource_engine_fastapi` | core, FastAPI | HTTP adapter: generated routes, document routes, bearer auth, error mapping, event push endpoint |
| `resource-engine-sqlalchemy` | `resource_engine_sqlalchemy` | core, SQLAlchemy 2 (async) | Persistence adapter: repository, unit of work, outbox, inbox, documents, permission and audit tables, job leases |
| `resource-engine-devtools` | `resource_engine_devtools` | core, docstring-parser | `resource-engine` CLI: inspect, check (architecture and hook/effect lints), docs, AI skill and context (`ai install`, `ai sync`), worker |
| `resource-engine-aws` | `resource_engine_aws` | core, `[sqs,sns]` → aiobotocore | SQS/SNS publishers, SQS consumer source |
| `resource-engine-azure` | `resource_engine_azure` | core, `[servicebus]` → azure-servicebus | Service Bus publisher and consumer source |
| `resource-engine-gcp` | `resource_engine_gcp` | core, `[pubsub]` → google-cloud-pubsub | Pub/Sub publisher, pull source, push decoder |
| `resource-engine-http` | `resource_engine_http` | core, httpx, `[soap]` → zeep | REST and SOAP repositories, webhook publisher, OAuth2 client credentials |
| `resource-engine-s3` | `resource_engine_s3` | core, aiobotocore | Document storage on S3-compatible stores (AWS S3, Garage, MinIO, R2) |
| `resource-engine-rabbitmq` | `resource_engine_rabbitmq` | core, aio-pika | RabbitMQ publisher (confirms) and quorum-queue consumer source |
| `resource-engine-oidc` | `resource_engine_oidc` | core, PyJWT, httpx | Bearer-token authentication: Keycloak, Cognito, Entra ID, Google |
| `resource-engine-opa` | `resource_engine_opa` | core, httpx | Open Policy Agent decisions and row filters (`OpaEngine`) |
| `resource-engine-cedar` | `resource_engine_cedar` | core, cedarpy | Cedar policies in process (`CedarEngine`) |
| `resource-engine-openfga` | `resource_engine_openfga` | core, openfga-sdk | OpenFGA relationships (`OpenFgaEngine`, `TupleSync`) |
| `resource-engine-opentelemetry` | `resource_engine_opentelemetry` | core, opentelemetry-api | Tracing (`OtelTracer`) and consumer metrics (`ConsumerMetrics`) |
| `resource-engine-sap` | `resource_engine_sap` | core, resource-engine-http | SAP Event Mesh (publisher, queue consumer) and advanced event mesh publisher |

Core packages are in `packages/`, cloud, SAP, storage, identity, policy-engine, observability and HTTP adapters in `adapters/` (see the
[adapter catalogue](docs/adapters.md)).

Adapters depend on the core, never the other way round.

## Quick look

```python
class InvoiceResource(Resource):
    """Gestió de factures de l'organització."""

    model = Invoice  # plain domain class
    schemas = {"create": InvoiceCreate, "update": InvoiceUpdate, "read": InvoiceRead}
    operations = ["list", "get", "create", "update", "delete"]
    state_machine = InvoiceStateMachine  # python-statemachine
    transition_schemas = {"reject": RejectInvoice}
    actions = [RecalculateTotals]
    hooks = {"before_update": [ensure_editable], "after_commit": [notify_approval]}
    filterable = ["status", "customer"]


registry = ResourceRegistry()
registry.register(InvoiceResource)

manager = ResourceManager(registry, unit_of_work_factory(session_factory, registry))
mount_resources(app, registry, manager)  # FastAPI
```

This exposes `GET/POST /invoices`, `GET/PATCH/DELETE /invoices/{id}`,
`GET /invoices/{id}/transitions`, `POST /invoices/{id}/transitions/{event}` and
`POST /invoices/{id}/actions/recalculate_totals`.

The complete reference module is in [`examples/invoices`](examples/invoices) and is
part of the test suite.

## Development

```bash
uv sync                      # create .venv with all workspace packages
uv run pytest                # tests (emulator tests skip if emulators are down)
docker compose -f compose.emulators.yaml up -d --build   # AWS Emulator + Pub/Sub emulator
uv run ruff check . && uv run ruff format --check .
uv run resource-engine inspect
uv run resource-engine docs validate
uv run resource-engine docs generate --out examples/invoices/docs
uv run uvicorn invoices.app:app --app-dir examples --reload   # then open /docs
```

## Documentation

- [Proposal (v3)](docs/proposal-v3.md) — goals and design principles (Catalan).
- [Architecture](docs/architecture.md) — layers, packages and repository strategy.
- [Lifecycle contract](docs/lifecycle.md) — exact order of hooks, validation and commit.
- [Error catalogue](docs/errors.md) — stable `RE-*` codes.
- [Messaging](docs/messaging.md) — envelopes, outbox, relay, consumers with exactly-once effects, metrics and retention.
- [Adapter catalogue](docs/adapters.md) — cloud adapters, contract tests, emulators.
- [Documents](docs/documents.md) — slots, uploads, processing, clean-up.
- [Identity](docs/identity.md) — bearer tokens, providers, `Principal`.
- [Permissions](docs/permissions.md) — system roles, ABAC policies managed from a front end.
- [External policy engines](docs/authorizers.md) — OPA, Cedar and OpenFGA behind the same port.
- [AI assistance](docs/ai.md) — the skill for coding agents, `ai install`, `ai sync`.
- [Observability](docs/observability.md) — traces from request to consumer, metrics hooks.
- [Background jobs](docs/jobs.md) — worker process, periodic jobs, services, one worker at a time.
- [Audit trail](docs/audit.md) — committed changes and refused attempts, tamper-evident seals, retention.
- [Tenancy](docs/tenancy.md) — tenant-scoped resources, cross-tenant roles.
- [Concurrency](docs/concurrency.md) — version field, `ETag` / `If-Match`, stale writes refused.
- [Roadmap](docs/roadmap.md) — what is done and what comes next.
