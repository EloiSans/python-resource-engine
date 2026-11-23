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

## Delivery modes

| Mode | Class | Guarantee |
|---|---|---|
| Direct | `AfterCommitDelivery` (default; wraps `event_publisher`) | Best effort: events are lost if the process dies between commit and publish. |
| <a id="outbox"></a>Outbox | `OutboxDelivery` + `OutboxRelay` | At-least-once: envelopes are written to the outbox in the same transaction (lifecycle step 9) and relayed later. |

The outbox needs a unit of work with `outbox()` (`SupportsOutbox`):
`InMemoryUnitOfWork`, or `SqlAlchemyUnitOfWork(..., outbox_table=outbox_table(metadata))`.
Using `OutboxDelivery` with another unit of work fails with RE-E403.

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
from resource_engine.messaging import EventRouter, MessageConsumer, MessageProcessor, RetryPolicy
from resource_engine_aws import SqsMessageSource

router = EventRouter()


@router.on("PaymentReceived")  # published by the payments service
async def mark_paid(envelope):
    await manager.transition("invoices", envelope.payload["invoice_id"], "pay")


processor = MessageProcessor(
    router,
    name="invoices-payments",  # inbox entries are per consumer
    inbox=uow_factory,  # unit of work with inbox(); optional
    retry=RetryPolicy(max_deliveries=5, base_delay=1, max_delay=300),
)
consumer = MessageConsumer(SqsMessageSource(sqs, queue_url), processor)
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

### Ordering

Messages of a batch are processed in order. When one fails, later messages with
the same `key` in that batch are retried without running, so per-entity order is
kept on FIFO queues, Service Bus sessions and Pub/Sub ordering keys.

### Duplicates

Delivery is at-least-once. With `inbox=`, a message is skipped when its envelope
id is already recorded for that consumer. The id is recorded **after** the handler
succeeds, in its own transaction, so a crash between the two can still run a
handler twice: keep handlers idempotent where you can (for example, transitions
fail with RE-E201 when repeated, which the handler can treat as done). Running
handler and inbox in one transaction needs the manager to join an outer unit of
work; it is on the roadmap.

Enable the inbox with `SqlAlchemyUnitOfWork(..., inbox_table=inbox_table(metadata))`
or the in-memory unit of work. Old rows can be deleted once older than the
broker's redelivery window.

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

## CloudEvents

`resource_engine.messaging.to_cloudevent(envelope, source=..., type_prefix=...)`
maps an envelope to a structured-mode CloudEvents 1.0 object, and
`from_cloudevent` maps it back without loss. `key` travels as `subject` and as the
`partitionkey` extension; `correlation_id` and `resource` as the `correlationid`
and `resource` extensions. The webhook and SAP publishers use it with
`format="cloudevents"`.
