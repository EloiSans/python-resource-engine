# Process managers

Some business processes span several resources and take time. Paying an
approved invoice starts when the invoice is approved, waits for the payments
service, marks the invoice as paid, and raises an alarm if nothing arrives in 15
days. A process manager (or saga) holds that process.

In Resource Engine **a process is a resource**:
- **Records:** its records are the running instances, one per correlation key
  (one per invoice).
- **State machine:** it holds the steps.
- **Timers:** they are the timeouts (see [timers](timers.md)).
- **Hooks:** they hold the compensations.
- **Events:** it reacts to them with handlers that move the instance and act
  on other resources through the manager.

Everything a resource gets, a process gets too: storage, permissions, the audit
trail and the history of every step, read access over REST, GraphQL and MCP,
and generated docs with its state diagram.

```python
from datetime import timedelta

from resource_engine import Reference, Timer
from resource_engine.processes import On, Process, ProcessStep


async def start_payment(step: ProcessStep) -> None:
    """When an invoice is approved, wait for its payment."""
    invoice = await step.manager.get("invoices", step.key, actor=step.actor)
    await step.start({"amount": invoice.amount}, tenant=invoice.organization)


async def record_payment(step: ProcessStep) -> None:
    """A payment from the payments service settles the process and the invoice."""
    if not step.can("receive"):
        return  # already settled: a repeated notification
    await step.transition("receive")
    await step.manager.transition("invoices", step.key, "pay", actor=step.actor)


class InvoicePaymentProcess(Process):
    name = "invoice_payments"
    model = InvoicePayment
    schemas = {"create": PaymentStart, "read": PaymentRead}
    operations = ["list", "get", "create"]
    state_machine = PaymentFlow  # awaiting → settled, awaiting → overdue → settled
    state_since_field = "status_changed_at"
    timers = [Timer("expire", after=timedelta(days=15))]
    hooks = {"after_transition:expire": [announce_overdue]}
    references = {"invoice_id": Reference("invoices", related_name="payments")}
    correlation = "invoice_id"
    reacts = [
        On(
            "TransitionExecuted",
            start_payment,
            resource="invoices",
            transition="approve",
            start=True,
        ),
        On("PaymentReceived", record_payment, key="invoice_id", payload=PaymentReceived),
    ]
```

The full example is `examples/invoices/application/processes.py`.

## Declaring a process

| Attribute | Meaning |
|---|---|
| `correlation` | The field that ties events to an instance; one instance per value. It must be in the create and read schemas, and `step.start()` sets it. |
| `reacts` | `On(...)` declarations |
| `operations` | Needs `create` and `list` (the process creates and finds its instances); `get` for readers |
| `internal_operations` | `("create",)` by default: only the process creates instances, and the API never exposes `POST /invoice_payments` |
| `expose_transitions` | `False` by default: the process moves itself; its transitions have no routes |

The usual resource attributes apply: `state_machine`, `timers`, `hooks`,
`events`, `references`, `tenant_field`, `filterable`, permissions.

`On(event, handler, ...)`:

| Argument | Meaning |
|---|---|
| `event` | Event type, such as `"PaymentReceived"`, or a `DomainEvent` class |
| `key` | Where the correlation key is: a dotted path in the payload (default `entity_id`, the record of an engine event), or `(envelope) -> key` |
| `resource`, `transition`, `action` | Only engine events of that resource, transition or action |
| `start` | Run even when no instance has this key, so the handler can call `step.start()`. Otherwise such events are ignored. |
| `payload` | A model the payload is validated against; `step.payload` is the parsed value. Invalid payloads are dead-lettered. |

## Handlers

A handler receives a `ProcessStep`:

| | |
|---|---|
| `step.envelope`, `step.payload`, `step.key` | The event, its (parsed) payload and the correlation key |
| `step.instance` | The process record, or `None` before it starts |
| `await step.start(data, tenant=)` | Create the instance for this key; it returns the existing one if there is one |
| `await step.transition(event, data)` | Move the instance |
| `step.can(event)` | Whether the instance's state allows the transition now |
| `step.manager`, `step.actor` | Run any other operation as the process (`system:processes`) |

Every event is handled in one transaction with the inbox: the instance's
changes, the other resources' changes, their audit entries, their outbox events
and the mark that the message was processed commit together or not at all.

- **Duplicates:** a message delivered twice is acknowledged as a duplicate and
  doesn't run again.
- **Failures:** a failing handler rolls everything back, and the message is
  retried with backoff.
- **Repeated notifications:** handlers should still tolerate a business-level
  repeat (two "payment received" events for one invoice), for example with
  `step.can(...)`.

## Compensations and timeouts

A compensation is an explicit step of the process, not something hidden:
- **Make it a transition** with a hook. For example, `expire` with an
  `after_transition:expire` hook that records `PaymentOverdue`, or a `cancel`
  that undoes what earlier steps did through the manager.
- **Make timeouts timers** on the process, run by `TimerJob`.
- **Where it shows:** in the state diagram, the history and the audit trail.

## Running processes

`ProcessRunner(manager).router()` is one `EventRouter` for every process
registered with the manager. Run it through a `MessageProcessor` with
`manager=`, which gives the exactly-once transaction.

**One service, no broker of its own:** `ProcessFeed` feeds the outbox to the
processes. Write events to the outbox (`OutboxDelivery`) and add the feed to
the worker:

```python
processor = MessageProcessor(ProcessRunner(manager).router(), name="processes", manager=manager)
runner.add("processes", ProcessFeed(uow_factory, processor), every=1, single=True)
runner.add("timers", TimerJob(manager), every=timedelta(minutes=1), single=True)
```

- **Batches:** each run reads a batch of pending outbox records, handles each
  in its own transaction, and marks them published, or failed and retried on
  later runs up to `max_attempts`.
- **Also to a broker:** `ProcessFeed(..., then=publisher)` also publishes the
  batch to a broker, so one job empties the outbox. Use it instead of
  `OutboxRelay`, which keeps its transaction open while it publishes.

**With a broker:** the processes consume like any other consumer:

```python
consumer = MessageConsumer(SqsMessageSource(sqs, queue_url), processor)
```

Events from other services, such as `PaymentReceived` from the payments
service, arrive through a consumer or the push endpoint and go through the same
processor.

The example's worker (`examples/invoices/worker.py`, `resource-engine
worker`) runs `ProcessFeed` and `TimerJob`.

## Tenancy

Processes act as a system principal, across tenants. When the process resource
is tenant-scoped, pass the tenant when it starts: `step.start(data,
tenant=invoice.organization)`. Users then see only their organization's
processes, like any tenant-scoped resource.

## Limits

- **One instance per key:** a key that must run several times (one per
  attempt) needs a key that includes the attempt.
- **Finding instances:** the instance is found by the correlation field, which
  needs an index (and is unique in the example's table).
- **Long chains:** a handler that calls many operations is still one
  transaction. Split long chains into steps that react to each other's events.
