# Messaging

## Envelope

Events recorded with `ctx.record_event` are serialised by `EventSerializer` into
an `EventEnvelope`:

```json
{"id": "9f…", "type": "TransitionExecuted", "occurred_at": "2026-11-21T08:00:00+00:00",
 "correlation_id": "c1…", "resource": "invoices", "key": "invoices:42",
 "schema_version": "1", "payload": {"resource": "invoices", "entity_id": 42,
 "transition": "submit", "source": "draft", "target": "pending"}}
```

`Decimal`, dates, UUIDs, enums, dataclasses and Pydantic models are converted to
JSON-safe values. `key` (`<resource>:<entity_id>`) is the ordering key used by
FIFO queues, sessions and ordering keys.

With a tracer configured, envelopes also carry `"trace": {"traceparent": …}`,
so consumers continue the producer's trace ([observability](observability.md#from-the-operation-to-the-consumer)).

### Declaring events

Declare the domain events a resource's actions and hooks record:

```python
class InvoiceResource(Resource):
    actions = [RecalculateTotals]
    events = [InvoiceTotalsRecalculated]  # DomainEvent dataclasses
```

- **Where they appear:** declared events are listed in the generated docs and
  in the [AsyncAPI document](asyncapi.md).
- **Undeclared events:** they are still published. `resource-engine check`
  reports them as RE-C006.
- **Engine events:** created, updated, deleted, transition, action and document
  events need no declaration.
- **Validation:** an entry that isn't a `DomainEvent` dataclass, or two events
  with the same class name (the type on the wire), is refused at registration.

## Delivery modes

| Mode | Class | Guarantee |
|---|---|---|
| Direct | `AfterCommitDelivery` (default; wraps `event_publisher`) | Best effort: events are lost if the process dies between commit and publish. |
| <a id="outbox"></a>Outbox | `OutboxDelivery` + `OutboxRelay` | At-least-once: envelopes are written to the outbox in the same transaction (lifecycle step 9) and relayed later. |

The outbox needs a unit of work with `outbox()` (`SupportsOutbox`):
`InMemoryUnitOfWork`, or `SqlAlchemyUnitOfWork(..., outbox_table=outbox_table(metadata))`.
Using `OutboxDelivery` with another unit of work fails with RE-E403.
Envelopes may carry `tags`, extra string attributes such as `origin=console`
on events published by hand from the [explorer](explorer.md#pub). They are
sent as broker attributes and CloudEvents extensions.
`OutboxDelivery(live=hub)` also hands the same envelopes to an `EventHub` in
this process right after the commit, for [live subscribers](#live-subscribers)
such as the explorer. It is best effort, and the outbox stays the source of
delivery.

`OutboxRelay.run_once()` reads pending records (locked with `SKIP LOCKED` on SQL
databases that support it), publishes them, marks delivered ones as published and
increments `attempts` on failures. Records reaching `max_attempts` are left for
inspection.

## Consumers

The inbound side mirrors the outbound one: broker-neutral logic in the core,
one small adapter per broker.

```text
broker ──► MessageSource (adapter)  ──► MessageConsumer ─┐
                                                          ├─► MessageProcessor ─► EventRouter ─► handlers
HTTP push ──► build_event_receiver (FastAPI) ────────────┘          │
                                                               InboxStore (unit of work)
```

| Piece | Where | Role |
|---|---|---|
| `MessageSource` | port; adapters per broker | `receive`, `ack`, `retry(delay)`; optional `dead_letter` (`SupportsDeadLetter`). |
| `MessageProcessor` | core | Decides the outcome of one message: ack, duplicate, retry (with backoff) or dead-letter. |
| `MessageConsumer` | core | Pull loop: `run_once()` / `run_forever(stop)`; settles each batch. |
| `build_event_receiver` | `resource-engine-fastapi` | Push endpoint using the same processor. |
| `InboxStore` / `SupportsInbox` | port; in-memory and SQLAlchemy | Envelope ids already processed, per consumer name. |

```python
from resource_engine.messaging import (
    EventRouter,
    LoggingObserver,
    MessageConsumer,
    MessageProcessor,
    RetryPolicy,
)
from resource_engine_aws import SqsMessageSource

router = EventRouter()


@router.on("PaymentReceived")  # published by the payments service
async def mark_paid(envelope):
    await manager.transition("invoices", envelope.payload["invoice_id"], "pay")


processor = MessageProcessor(
    router,
    name="invoices-payments",  # inbox entries are per consumer
    manager=manager,  # handler + inbox in one transaction (exactly-once effects)
    retry=RetryPolicy(max_deliveries=5, base_delay=1, max_delay=300),
    observers=[LoggingObserver()],  # metrics hooks
)
consumer = MessageConsumer(SqsMessageSource(sqs, queue_url), processor, concurrency=4)
await consumer.run_forever(stop_event)
```

### Outcomes

| Situation | Outcome | Broker action |
|---|---|---|
| Handler returns | `ACK` | ack; the id is recorded in the inbox |
| Id already in the inbox | `DUPLICATE` | ack, handler not called |
| Handler raises (transient) | `RETRY` | `retry(delay)` with exponential backoff |
| Delivery count reaches `max_deliveries` | `DEAD_LETTER` | `dead_letter` if the source supports it, else left to the broker's redrive policy |
| Undecodable body (RE-E405), no handler (RE-E402), `MessageRejected` (RE-E406), `DomainError`, `ValidationFailed` | `DEAD_LETTER` | at once, no retries |

Raise `MessageRejected` from a handler when a message can never succeed.
`permanent_errors=` changes the list of non-retryable exceptions.

### Typed payloads

Events from other services arrive as dicts. Give the router a payload type
(a Pydantic model, a dataclass, or anything Pydantic validates) to validate
them before the handler runs:

```python
class PaymentReceived(BaseModel):
    invoice_id: int
    amount: Decimal


@router.on("PaymentReceived", payload=PaymentReceived)
async def mark_paid(envelope, payment: PaymentReceived):
    await manager.transition("invoices", payment.invoice_id, "pay")
```

- **Handler arguments:** a handler registered with `payload=` gets
  `(envelope, payload)`. Untyped handlers still get only the envelope.
- **Invalid payloads:** they raise `MessageRejected` (RE-E406) with
  Pydantic's errors in `details`, so the message is dead-lettered without
  retries. Fix the producer or the model, then replay it.
- **One type per event:** an event type has one payload type. Registering a
  different one is a `ConfigurationError`.
- **Documentation:** the type is the message schema in the
  [AsyncAPI document](asyncapi.md). `EventRouter(name=..., description=...)`
  names and describes the consumer's channel.

### Ordering

Messages of a batch are processed in order. When one fails, later messages with
the same `key` in that batch are retried without running, so per-entity order is
kept on FIFO queues, Service Bus sessions and Pub/Sub ordering keys.

With `MessageConsumer(..., concurrency=4)`, up to 4 keys of a batch run at the
same time. Each key still runs in order, and messages without a key are
independent. Every message has its own transaction.

### Exactly-once effects

Delivery is at-least-once. With `manager=`, each message runs in
[`manager.transaction()`](lifecycle.md#grouping-operations):

1. If the envelope id is already in the inbox for this consumer, the result is
   `DUPLICATE`.
2. The handler runs. Every manager call it makes joins the transaction.
3. The id is checked again, then recorded.
4. Everything commits together.

A crash, or a handler error, rolls back the handler's writes and the inbox mark
together, so the redelivery starts clean. The changes, their audit entries and
their outbox events exist only if the message is recorded as processed.

Two consumers racing on the same message:

- If the first has already committed, the second sees the id on its second
  check and rolls back (`DUPLICATE`).
- If the first hasn't committed yet, the inbox primary key fails the second
  commit (`RETRY`), and its redelivery is a `DUPLICATE`.

This covers the manager's writes. Calls to other services from a handler are
not undone; put them in `after_commit` hooks or behind the outbox.

`inbox=uow_factory` without `manager=` keeps the older mode. Duplicates are
skipped, but the id is recorded **after** the handler, in its own transaction,
so a crash between the two can run a handler twice.

Enable the inbox with `SqlAlchemyUnitOfWork(..., inbox_table=inbox_table(metadata))`
or the in-memory unit of work. Old marks are deleted by the messaging janitor
([retention](#retention)).

### Metrics

`observers=[...]` are called after every message with `(consumer, message,
result, seconds)`, sync or async. That includes duplicates, dead letters, and
messages held back behind a failed key (0 seconds). A failing observer is
logged and ignored. `LoggingObserver` writes one line per message to
`resource_engine.messaging.metrics`. Held-back messages have
`result.blocked` set. `ActivityMessageObserver(activity_log)` records each
consumer's outcome per message in an `ActivityLog`, for the
[explorer](explorer.md#which-consumers-handled-an-event).

For OpenTelemetry, install `resource-engine-opentelemetry`:

```python
from resource_engine_opentelemetry import ConsumerMetrics

processor = MessageProcessor(router, name="billing", manager=manager, observers=[ConsumerMetrics()])
```

| Instrument | Kind | Attributes |
|---|---|---|
| `resource_engine.consumer.messages` (`{message}`) | Counter | consumer, outcome, event type, `blocked` |
| `resource_engine.consumer.duration` (`s`) | Histogram | consumer, outcome, event type |

The attributes are named `resource_engine.consumer`, `resource_engine.outcome`,
`resource_engine.event_type` and `resource_engine.blocked`. Held-back messages
are counted but not timed. Pass `include_event_type=False` when there are many
event types. The package needs only `opentelemetry-api`. Configure the SDK and
an exporter in your application; without them the instruments do nothing.

### Retention

Inbox marks and published outbox records pile up. `MessagingJanitor` deletes
them once they are past their retention:

```python
from datetime import timedelta

from resource_engine.messaging import MessagingJanitor

janitor = MessagingJanitor(
    uow_factory, inbox_retention=timedelta(days=14), outbox_retention=timedelta(days=7)
)
await janitor.run_once()  # from a scheduled job, or: await janitor.run_forever(interval=3600)
```

- **Inbox:** keep marks longer than the broker can redeliver a message
  (message retention, dead-letter redrive). A message that comes back after its
  mark is deleted runs again.
- **Outbox:** only published records are deleted. Pending and failing records
  stay until the relay sends them.
- **Batches:** `batch_size` rows per transaction (1000 by default), repeated
  until a batch comes back short.
- **Opting out:** `None` leaves a table alone. A unit of work without the table
  is a configuration error unless its retention is `None`.
- **Activity log:** `activity=` an `ActivityLog` is purged after
  `activity_retention` (7 days).

Custom stores implement `InboxStore.purge(before, limit)` and
`OutboxStore.purge_published(before, limit)`. `InboxContract` and
`OutboxContract` test both.

### Push endpoints

```python
from resource_engine_fastapi import build_event_receiver

app.include_router(
    build_event_receiver(processor, path="/events", dependencies=[Depends(verify_caller)])
)
```

`204` acks, `503` + `Retry-After` asks for redelivery, `422` (configurable with
`dead_letter_status`) marks a message that must not be retried. The route accepts
the envelope JSON and CloudEvents (structured and binary). `OPTIONS` answers the
CloudEvents webhook handshake for `allowed_origins`. Use `decode=` for wrapped
formats, e.g. `resource_engine_gcp.decode_pubsub_push`.

## Live subscribers

`EventHub` fans committed events out to subscribers in the same process
(GraphQL subscriptions, server-sent events, WebSockets). It is a
`MessagePublisher`, so it is fed like a broker:

- straight after each commit: `ResourceManager(event_publisher=TransportEventPublisher(hub))`;
- from the outbox, next to the broker: `OutboxRelay(uow_factory, FanoutPublisher(broker, hub))`.
  `FanoutPublisher` sends to every publisher and reports the ids any of them
  rejected, so the relay retries them;
- with several instances, from a broadcast topic each instance consumes:
  `MessageConsumer(source, MessageProcessor(hub.router(), name="live"))`.

`hub.subscribe(filter)` returns a `Subscription` to iterate with `async for`.
Each holds `queue_size` envelopes (default 100); when it is full the oldest is
dropped and `overflowed` counts it. Subscribers must check permissions
themselves. `LiveEvents(manager, hub)` does it for the GraphQL subscriptions
and the [explorer's event stream](explorer.md#events): it re-reads each record
as the subscriber, never reveals the ids of records the subscriber didn't
see, and shows payloads and events about no record only with `events:read`.

## CloudEvents

`resource_engine.messaging.to_cloudevent(envelope, source=..., type_prefix=...)`
maps an envelope to a structured-mode CloudEvents 1.0 object, and
`from_cloudevent` maps it back without loss. `key` travels as `subject` and as the
`partitionkey` extension; `correlation_id` and `resource` as the `correlationid`
and `resource` extensions. The webhook and SAP publishers use it with
`format="cloudevents"`.
