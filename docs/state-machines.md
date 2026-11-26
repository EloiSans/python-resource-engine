# State machines

Resources with a process use [`python-statemachine`](https://python-statemachine.readthedocs.io/)
3. The engine stores the state on the entity, sets the initial state on create
and runs each transition as an operation: its own permission
(`<resource>:transition:<event>`), hooks (`before_transition:<event>`,
`after_transition:<event>`), audit entry and `TransitionExecuted` event.

```python
class InvoiceResource(Resource):
    state_machine = InvoiceStateMachine
    state_field = "status"  # the default
    state_since_field = "status_changed_at"  # optional: when it entered its state
    transition_schemas = {"reject": RejectInvoice}
```

Run them with `manager.transition(...)`, `POST /invoices/{id}/transitions/<event>`,
the GraphQL mutation `<event>Invoice` or the MCP tool
`invoices_transition_<event>`. `allowed_transitions` lists the events the
caller may run now (the current state has them, their guards pass without
input and the caller holds the permission). `history` reads the record's
state changes from the audit trail ([audit](audit.md#history)).

## Several machines

A record can follow independent processes, each with its own state: an
invoice's approval (`draft → pending → approved`) and its delivery to the
customer (`unsent → sent → returned`). Declare them by name with `Machine`:

```python
from resource_engine import Machine, Resource


class InvoiceResource(Resource):
    machines = {
        "approval": Machine(InvoiceStateMachine, field="status", since_field="status_changed_at"),
        "delivery": Machine(InvoiceDelivery, field="delivery_status"),
    }
```

- **Fields:** each machine has its own state field (in the `read` schema, never
  in `create` or `update`) and optionally its own `since_field`. Create sets
  every machine's initial state.
- **Transitions:** names are unique across the resource's machines (checked at
  registration), so routes, permissions, GraphQL mutations and MCP tools keep
  the plain event name: `POST /invoices/7/transitions/send`,
  `invoices:transition:send`. The engine finds the machine from the event.
- **Guards across machines:** a guard reads the entity (`self.model`), so it
  can depend on another machine's state:

  ```python
  class InvoiceDelivery(StateMachine):
      send = unsent.to(sent, cond="invoice_approved")

      def invoice_approved(self) -> bool:
          return self.model.status in ("approved", "paid")
  ```

  A failing guard is RE-E201, as for a state without the transition.
- **Events:** `TransitionExecuted` carries `machine` (its name). With the
  shorthand, the one machine is named after its field (`status`).
- **History:** each change names its `machine`, and creation gives one entry
  per machine. Filter with `manager.history(..., machine="delivery")`,
  `GET /invoices/7/history?machine=delivery`, the GraphQL `history(machine:)`
  argument or the MCP tool's `machine`.
- **Timers:** a `Timer` belongs to the machine of its event. It counts from that
  machine's `since_field` and checks that machine's state.
- **Docs:** `docs generate` draws one diagram per machine, and the permissions
  catalogue offers each state field as a condition attribute.

`state_machine` and `machines` are exclusive. `definition.machines` has the
runners by name, `definition.machine_for(event)` the one with an event and
`definition.machine` the first.

## Protected state

State fields cannot appear in `create` or `update` schemas (RE-E004) and
`apply_update` ignores them: state changes only through transitions.

See [timers](timers.md) for deadlines and [processes](processes.md) for flows
across resources.
