# State machines

Processes with states use `python-statemachine` 3, in `domain/state_machine.py`.
The engine stores the state in `state_field` (default `status`), sets the
initial state on create and runs each transition as an operation with its own
hooks, permission and event.

```python
from statemachine import State, StateMachine


class InvoiceStateMachine(StateMachine):
    """Procés d'aprovació i cobrament d'una factura.

    States:
        draft: Esborrany editable per l'emissor.
        pending: Enviada a aprovació; ja no es pot modificar.
        approved: Aprovada i pendent de cobrament.
        rejected: Rebutjada pel responsable.
        paid: Cobrada. Estat final.

    Transitions:
        submit: Envia l'esborrany a aprovació.
        approve: Aprova una factura pendent.
        reject: Rebutja una factura pendent indicant-ne el motiu.
        reopen: Torna una factura rebutjada a esborrany.
        pay: Registra el cobrament d'una factura aprovada.
    """

    draft = State("Esborrany", initial=True)
    pending = State("Pendent")
    approved = State("Aprovada")
    rejected = State("Rebutjada")
    paid = State("Cobrada", final=True)

    submit = draft.to(pending)
    approve = pending.to(approved)
    reject = pending.to(rejected)
    reopen = rejected.to(draft)
    pay = approved.to(paid)

    def on_reject(self, reason: str) -> None:
        self.model.rejection_reason = reason  # transition input arrives as arguments
```

On the resource:

```python
state_machine = InvoiceStateMachine
transition_schemas = {"reject": RejectInvoice}  # input of `reject`: {"reason": ...}
```

## Rules

- **Every state and every transition is documented** in the `States:` and
  `Transitions:` sections (RE-D004, RE-D005).
- **The state field is not in `create` or `update` schemas** (RE-E004), and
  updates never change it. Put it in the `read` schema.
- **Callbacks change the entity (`self.model`) only.** No I/O, no events:
  record events in `after_transition[:event]` hooks.
- **Guards** (`cond=`, `unless=`) are fine for state-based conditions. Rules
  about who may transition belong to permissions
  (`invoices:transition:approve`), not guards.
- Transitions that need documents use `DocumentSlot(required_for=["approve"])`
  (RE-E606 when missing).
- `expose_transitions = False` keeps them internal (consumers, jobs), with no
  HTTP routes.

## Running them

```python
await manager.transition("invoices", 7, "reject", {"reason": "Import incorrecte"}, actor=user)
await manager.allowed_transitions("invoices", 7, actor=user)  # what the caller may fire now
```

HTTP: `GET /invoices/{id}/transitions`, `POST /invoices/{id}/transitions/reject`.
Not possible from the current state → RE-E201 (`details.allowed`); unknown
event → RE-E202. The engine publishes `TransitionExecuted` (with `source` and
`target`) after commit.

`resource-engine docs generate` draws each machine as a Mermaid diagram.
