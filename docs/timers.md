# Timed transitions

Business processes have deadlines: a quote expires after 30 days, an unpaid
invoice gets a reminder, an approval that nobody acts on is escalated. A resource
declares them as timers on its state machine, and the worker runs them.

```python
from datetime import timedelta

from resource_engine import Resource, Timer


class InvoiceResource(Resource):
    state_machine = InvoiceStateMachine
    state_since_field = "status_changed_at"  # when the record entered its current state
    timers = [
        Timer(
            "reject",
            after=timedelta(days=30),
            data={"reason": "No answer in 30 days"},
            description="Invoices pending approval for more than 30 days are rejected.",
        )
    ]
```

## Declaring a timer

| Argument | Meaning |
|---|---|
| `event` | The transition to run |
| `after` | How long the record waits first |
| `since` | Datetime field the wait counts from; default `state_since_field` (or the event's `Machine.since_field`) |
| `states` | States the record must be in; default: the states the event runs from |
| `data` | The transition's input, validated against its schema when the resource is registered |
| `description` | What the deadline means, for the generated docs |

- **`state_since_field`:** the engine sets this field to the current time
  when a record is created and on every transition, so "30 days in pending"
  needs no code. The field belongs to the entity and the read schema, and
  cannot be in the create or update schemas.
- **Several machines:** a timer belongs to the machine of its event: it checks
  that machine's state field and counts from its `since_field`
  ([state machines](state-machines.md#several-machines)).
- **Deadlines from other fields:** use `since=` with any other datetime
  field, such as `since="due_date"` for "7 days after the due date". The same
  transition may have several timers, for example a reminder and an expiry
  from different states.
- **Refused declarations:** an unknown transition, a state the event cannot
  run from, data that doesn't match the schema, a missing `since` field, or a
  non-positive `after`.

## Running them

`TimerJob` is a `JobRunner` job:

```python
from datetime import timedelta

from resource_engine import TimerJob

runner.add("timers", TimerJob(manager), every=timedelta(minutes=1), single=True)
```

Each run, for every timer:

1. **Find due records.** It lists the records in the timer's states whose
   `since` is older than `after`. That's one query with a row condition, so
   the repository must support conditions (in-memory, SQLAlchemy). Index the
   `since` column.
2. **Run the transition.** It calls `manager.transition` for each one, up to
   `batch` per timer (100 by default); the rest wait for the next run. The
   caller is the system principal `system:timers`, or `actor=`.
3. **Treat it as any transition:**
   - guards, hooks, the `after_transition` and `after_commit` hooks, events,
     the audit entry and the record's history all apply;
   - the history shows who ran it (`system:timers`);
   - tenancy doesn't stop it, because system principals act across tenants.

Edge cases:
- **Already moved on:** a record that left the state between the query and
  the transition (an approver acted at the same moment) is skipped.
- **Failures:** other failures are logged on `resource_engine.timers` and
  retried on the next run. The run's result counts what happened:
  `TimerRun(fired, skipped, failed, due)`, where `due` counts the due records
  per timer.
- **One worker:** use `single=True` with a lock provider (`SqlAlchemyLocks`),
  so one worker fires each deadline. A crash after a transition committed
  doesn't fire it twice, because the record is no longer in the timer's state.

Timers aren't exact: a record is handled on the first run after its deadline.
With `every=timedelta(minutes=1)` that is within a minute, and a backlog
drains `batch` records per timer per run.

## For one record

`pending_timers(manager, "invoices", 7, actor=user)` lists the timers waiting
on a record now: those whose states include its current state. Each comes
with its due time (`since` + `after`) and whether it is overdue.

- **Read as the caller:** the record is read with the caller's permission,
  row filter and tenant.
- **Hidden fields:** a `since` field the caller may not read gives no due
  time.
- **In the browser:** the [explorer's](explorer.md#jobs) `CRON` rows show the
  same list.

## In the docs and tools

- The generated functional docs list each resource's automatic transitions
  (after, from which states, counted from which field).
- The AI project catalogue carries a `timers:` line.
- `history` shows the transitions they ran.
