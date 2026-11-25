# AsyncAPI

The engine generates an [AsyncAPI 3.0](https://www.asyncapi.com/docs/reference/specification/v3.0.0)
document for the events an application publishes and the events it consumes,
from the same definitions as the code. It plays the same role for messaging as
OpenAPI does for the HTTP API: other teams, code generators and catalogues read
it to know which messages exist and what is in them.

```bash
resource-engine docs asyncapi --out docs/asyncapi.json   # or standard output
resource-engine docs generate                            # writes asyncapi.json with the functional docs
```

```python
mount_asyncapi(app, registry, consumers=[payments])  # GET /asyncapi.json, next to /openapi.json
```

## What is in it

| Direction | Channel | Messages | Payload schemas from |
|---|---|---|---|
| Published (`send`) | one per resource that emits events | engine events and declared `Resource.events` | the resource (id type, transition and state names, actions, slots) and the event dataclasses |
| Consumed (`receive`) | one per `EventRouter` | each registered event type | the handler's `payload=` type; untyped handlers have an open object |

**Engine events per resource:**

| Event | Emitted when |
|---|---|
| `ResourceCreated` | `create` is enabled |
| `ResourceUpdated` | `update` is enabled; `changes` maps each changed field to `[old, new]` |
| `ResourceDeleted` | `delete` is enabled |
| `TransitionExecuted` | the resource has a state machine; `transition`, `source` and `target` are enums |
| `ActionExecuted` | the resource has actions; `action` is an enum |
| `DocumentAttached`, `DocumentDetached` | the resource has document slots |
| `DocumentProcessed`, `DocumentRejected` | a slot has `processing` |

**Declared domain events:** each event in `Resource.events` (see
[messaging](messaging.md#declaring-events)) is a message. Its payload comes from
the dataclass fields, typed as they appear on the wire: `Decimal` and dates as
strings. Its summary is the event's docstring.

**Each message has:**

- the envelope fields around the payload: `id`, `type` (a constant),
  `occurred_at`, `correlation_id`, `resource`, `key` (the ordering key),
  `schema_version` and `trace`;
- the broker attributes as `headers`;
- `correlationId` pointing at `correlation_id`.

## Formats

`format="envelope"` (the default) describes the engine's envelope JSON.
`format="cloudevents"` describes structured CloudEvents 1.0, as the webhook and
SAP publishers send with `format="cloudevents"`:

- `type` is `type_prefix` plus the event name;
- `source` is a constant when you give one;
- the payload is `data`;
- the content type is `application/cloudevents+json`.

You can set the format for the whole document, or per channel when one broker
gets CloudEvents and another gets envelopes. The format only changes the
document, so set the same one the publisher uses.

## Servers and channels

The engine does not know your broker addresses, because each publisher is
configured with its own topic or queue. Give them in
`[tool.resource-engine.asyncapi]` for the CLI, or as arguments to
`asyncapi_document` and `mount_asyncapi`:

```toml
[tool.resource-engine]
registry = "billing.bootstrap:registry"
consumers = ["billing.messaging:payments"]  # EventRouter, or a function returning one

[tool.resource-engine.asyncapi]
title = "Billing"
version = "1.4.0"
format = "envelope"                   # or "cloudevents" (with source and type_prefix)

[tool.resource-engine.asyncapi.servers.production]
host = "sns.eu-west-1.amazonaws.com"
protocol = "sns"

[tool.resource-engine.asyncapi.channels.invoices]
address = "arn:aws:sns:eu-west-1:123456789012:invoices"
servers = ["production"]

[tool.resource-engine.asyncapi.channels.billing-payments]
address = "https://sqs.eu-west-1.amazonaws.com/123456789012/billing-payments"
```

- **Channel names** are resource names for published events, and
  `EventRouter(name=...)` for consumed ones.
- **Default address:** the channel name.
- **Bindings:** a channel's `bindings` table is copied as-is into AsyncAPI
  bindings (`sqs`, `sns`, `amqp`, `googlepubsub`, `http`...).
- **Errors:** an unknown key, a server without `host` and `protocol`, a channel
  naming an unknown server, or a consumer named like a resource is refused.

`resources = [...]` limits the published side to some resources. Without it,
every resource that emits events is included, the engine's own permission
resources too.

## External event providers

The engine connects to brokers through adapters on both sides:

- **Outbound:**
  - `MessagePublisher` adapters cover SQS, SNS, Service Bus, Pub/Sub, RabbitMQ,
    SAP Event Mesh and advanced event mesh, and webhooks;
  - they are fed after commit, or through the outbox and `OutboxRelay` (see
    [messaging](messaging.md#delivery-modes) and [adapters](adapters.md)).
- **Inbound:**
  - `MessageSource` adapters cover SQS, Pub/Sub, Service Bus, RabbitMQ and
    Event Mesh, plus the HTTP push endpoint (`build_event_receiver`);
  - bodies are decoded as the engine's envelope or as CloudEvents 1.0, in
    structured or binary mode;
  - so any provider that speaks CloudEvents (Azure Event Grid, Knative, SAP,
    many webhooks) works without code. `type_prefix` strips its type
    namespace.

For events from another service:

1. Register a handler with a payload model that matches the provider's
   contract. Payloads that don't match are dead-lettered instead of reaching
   your code (see [typed payloads](messaging.md#typed-payloads)).
2. Name the router after the subscription (`EventRouter(name="billing-payments")`)
   and list it in `consumers`. The `receive` side of the AsyncAPI document then
   records what the service depends on.
3. If the provider sends neither the envelope nor CloudEvents (Stripe webhooks,
   raw storage notifications), write a decoder that builds an `EventEnvelope`
   and pass it to the push endpoint (`build_event_receiver(decode=...)`) or to
   your `MessageSource`.

## Limits

- **Validation:** the document is checked by the test suite for dangling
  references and structure, but not against the official AsyncAPI JSON Schema.
  Run `asyncapi validate` from the AsyncAPI CLI in your CI if you publish the
  document.
- **Undeclared events:** a domain event recorded but not declared is missing
  (`check` reports it as RE-C006).
- **Consumed schemas:** only typed handlers have a payload schema.
