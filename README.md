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
| `resource-engine-graphql` | `resource_engine_graphql` | core, graphql-core | GraphQL adapter: schema generated from the resources, batched relations, `mount_graphql` |
| `resource-engine-mcp` | `resource_engine_mcp` | core, mcp | MCP server: every operation as a tool for AI agents, over stdio or Streamable HTTP with bearer tokens |
| `resource-engine-sqlalchemy` | `resource_engine_sqlalchemy` | core, SQLAlchemy 2 (async) | Persistence adapter: repository, unit of work, outbox, inbox, documents, permission and audit tables, job leases, Alembic setup (`[alembic]`) |
| `resource-engine-devtools` | `resource_engine_devtools` | core, docstring-parser | `resource-engine` CLI: inspect, check (architecture and hook/effect lints), docs and AsyncAPI, AI skill and context (`ai install`, `ai sync`), scaffold (modules, consumers), worker, development MCP server (`[mcp]`) |
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
| `resource-engine-opentelemetry` | `resource_engine_opentelemetry` | core, opentelemetry-api | Tracing (`OtelTracer`), operation, consumer and job metrics |
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
    references = {"customer_id": Reference("customers", name="customer_record")}
    filterable = ["status", "customer_id"]


registry = ResourceRegistry()
registry.register(InvoiceResource)

manager = ResourceManager(registry, unit_of_work_factory(session_factory, registry))
mount_resources(app, registry, manager)  # FastAPI
```

This exposes `GET/POST /invoices`, `GET/PATCH/DELETE /invoices/{id}`,
`GET /invoices/{id}/transitions`, `POST /invoices/{id}/transitions/{event}`,
`GET /invoices/{id}/history` (with an audit trail), `?expand=customer_record`
and `?customer_record.country=ES` on reads, and
`POST /invoices/{id}/actions/recalculate_totals`; `mount_asyncapi` adds
`GET /asyncapi.json` for the events.

Front ends consume every resource the same way (paging, filters, `expand`,
`Idempotency-Key`, `ETag`, one error format); see the
[front-end guide](docs/frontend.md).

The complete reference module is in [`examples/invoices`](examples/invoices) and is
part of the test suite.

## AI setup

Resource Engine ships what a coding agent needs to follow its conventions:

- **A skill and agent instructions:** written by `resource-engine ai install`.
- **A development MCP server:** `resource-engine mcp` serves `check`, docs
  validation, error explanations and the scaffolds as tools.

Set both up once and commit the result, so every agent on the team works the
same way.

```bash
uv add --dev 'resource-engine-devtools[mcp]'
uv run resource-engine ai install --mcp --target claude,agents   # add cursor, copilot as needed
```

Or give your agent this prompt and let it do the setup:

```text
Set up this repository for Resource Engine development with AI agents.

1. Add resource-engine-devtools with the mcp extra as a development dependency
   (uv add --dev 'resource-engine-devtools[mcp]', or the project's tool).
2. Make sure pyproject.toml has a [tool.resource-engine] table with `registry`
   pointing to the application's ResourceRegistry ("pkg.bootstrap:registry"),
   and `paths` if the code is not importable from the root. Add `authz`,
   `policies` and `consumers` if the project has them. If it publishes events,
   add a [tool.resource-engine.asyncapi] table (title, version, servers and
   channel addresses) and write the document with
   `resource-engine docs asyncapi --out docs/asyncapi.json`.
3. Run `resource-engine ai install --mcp --target claude,agents` (add cursor
   and copilot if the team uses them). It installs the skill and registers the
   development MCP server in .mcp.json (and .cursor/mcp.json,
   .vscode/mcp.json). Read the installed SKILL.md.
4. Tell me to restart or reload my agent so it picks up the MCP server, and
   check that its tools (check, docs_validate, explain_error, scaffold_*) are
   listed.
5. Verify: `resource-engine check`, `resource-engine docs validate --strict`
   and the tests. Fix what they report, using the explain_error tool for codes.
6. Add `resource-engine ai sync --check` to CI next to check and docs validate,
   and, if docs/asyncapi.json is committed,
   `resource-engine docs asyncapi --check docs/asyncapi.json` so changes that
   would break the consumers of its events fail the build.
7. Summarise what you changed, and commit only when I ask.
```

See [AI assistance](docs/ai.md) for what the skill contains and how to keep it
current, and the [MCP server](docs/mcp.md) to let agents use the application
itself.

## Development

```bash
uv sync                      # create .venv with all workspace packages
uv run pytest                # tests (emulator tests skip if emulators are down)
docker compose -f compose.emulators.yaml up -d --build   # AWS Emulator + Pub/Sub emulator
uv run ruff check . && uv run ruff format --check .
uv run resource-engine inspect
uv run resource-engine docs validate
uv run resource-engine docs generate --out examples/invoices/docs
docker compose -f compose.emulators.yaml up -d keycloak          # the example app's sign-in
uv run uvicorn invoices.app:app --app-dir examples --reload   # then open /docs (docs/example-app.md)
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
- [GraphQL](docs/graphql.md) — a generated schema with relations, mutations and the same permissions.
- [Front-end guide](docs/frontend.md) — the HTTP conventions a front end builds on: lists, `expand`, writes, errors, permissions.
- [Relationships](docs/relationships.md) — references between resources: integrity, deletes, `expand`, related filters.
- [Contracts](docs/contracts.md) — consumers scaffolded from a provider's AsyncAPI, contract tests on both sides.
- [AsyncAPI](docs/asyncapi.md) — the events the app publishes and consumes, generated and served next to OpenAPI.
- [MCP server](docs/mcp.md) — operations as tools for AI agents, permissions per caller.
- [Scaffolding](docs/scaffold.md) — generate a module, action or state machine.
- [AI assistance](docs/ai.md) — the skill for coding agents, `ai install`, `ai sync`; [evaluation](evals/README.md).
- [Observability](docs/observability.md) — traces from request to consumer, metrics hooks, JSON logs.
- [Background jobs](docs/jobs.md) — worker process, periodic jobs, services, one worker at a time.
- [Audit trail](docs/audit.md) — committed changes and refused attempts, tamper-evident seals, retention.
- [Example app](docs/example-app.md) — run `invoices` with Keycloak sign-in, organizations as tenants, demo users.
- [Tenancy](docs/tenancy.md) — tenant-scoped resources, cross-tenant roles.
- [Idempotency keys](docs/idempotency.md) — safe retries of writes (`Idempotency-Key`).
- [Concurrency](docs/concurrency.md) — version field, `ETag` / `If-Match`, stale writes refused.
- [Migrations](docs/migrations.md) — Alembic autogenerate for your tables and the engine's.
- [Releasing](docs/releasing.md) — CI, per-package versions, publishing to PyPI.
- [Roadmap](docs/roadmap.md) — what is done and what comes next.

## Licence

[Apache License 2.0](LICENSE). Every package carries the same licence.
