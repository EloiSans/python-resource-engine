# Lifecycle

Every writing operation (create, update, delete, transition, action, attach,
detach) runs these steps. The order is a contract, covered by the engine's
tests.

| # | Step | In the transaction | Hook points |
|---|---|---|---|
| 1 | Resolve resource and operation | – | |
| 2 | Tenant scope; permission without the record (RE-E702) | – | |
| 3 | Validate input with the operation's schema (RE-E101) | no | `before_validate`, `after_validate` |
| 4 | Open the unit of work | yes | |
| 5 | Load the entity; permission on the record (404 if hidden); writable fields (RE-E706); version check (RE-E105) | yes | |
| 6 | Before mutation | yes | `before_create` `before_update` `before_delete` `before_transition[:event]` `before_action[:name]` `before_attach[:slot]` `before_detach[:slot]` |
| 7 | Mutate: add / apply update / delete / fire the state machine / `Action.execute` / store the document | yes | |
| 8 | After mutation | yes | `after_create` `after_update` `after_delete` `after_transition[:event]` `after_action[:name]` `after_attach[:slot]` `after_detach[:slot]` |
| 9 | Build the result, write the audit entry, stage events (outbox), commit | yes | |
| 10 | After commit | no | `ctx.on_commit` callbacks, `after_commit` hooks, then event delivery |

- Any exception in steps 3 to 9 aborts the operation; from step 4 it rolls the
  transaction back and no event is published.
- Generic hooks run before qualified ones: `before_transition` then
  `before_transition:approve`.
- On create, the entity gets the initial state before step 6.
- `after_commit` failures are logged and do not undo the operation.

## Hooks

```python
from resource_engine import OperationContext


def ensure_editable(ctx: OperationContext) -> None:
    """Només es poden modificar o eliminar factures en esborrany."""
    ctx.entity.ensure_editable()  # the rule lives in the domain
```

`fn(ctx)` or `async def fn(ctx)`. Registered on the resource:
`hooks = {"before_update": [ensure_editable], "after_transition:approve": stamp}`.
Unknown points or qualifiers fail at registration (RE-E001).

Choosing the point:

| You want to | Use |
|---|---|
| Normalise raw input | `before_validate` (`ctx.raw_input` is mutable) |
| Check a rule that needs the stored entity | `before_update`, `before_delete`, `before_transition[:event]` |
| Check a rule on the final values | `after_create`, `after_update` (raise to roll back) |
| Set derived fields | `before_create`, `before_update` (assign on `ctx.entity`) |
| Record a domain event | any point before commit (`ctx.record_event`) |
| E-mail, HTTP call, message to another system | an `after_commit` hook marked `@external_effect`, or a consumer of the event |

## What a hook sees (`OperationContext`)

| Attribute | Content |
|---|---|
| `operation`, `transition`, `action`, `slot` | What is running |
| `actor` | The caller (`Principal`), or `None` |
| `raw_input`, `data` | Input before and after validation |
| `entity_id`, `entity` | Target record (loaded or newly built) |
| `previous_state`, `new_state` | Around a transition |
| `services` | `ResourceManager(services={...})`: notifiers, clients… |
| `correlation_id` | Shared by logs, audit and events of the operation |
| `repository(name=None)` | Repository in the current transaction (not after commit) |
| `record_event(event)` | Publish after commit |
| `on_rollback(fn)`, `on_commit(fn)` | Compensation / clean-up callbacks |
| `documents` | `attach`, `detach`, `list` documents in this transaction |

## External effects

Code before commit must not do anything the rollback cannot undo. Mark
functions that do with `@external_effect`; the engine refuses them anywhere but
`after_commit` (RE-E104), and `resource-engine check` finds unmarked ones
(RE-C002):

```python
from resource_engine import external_effect


@external_effect
async def notify_approval(ctx: OperationContext) -> None:
    """Notifica el client que la factura ha estat aprovada.

    Effects:
        Envia una notificació mitjançant el servei ``notifier``.
    """
    if ctx.transition == "approve":
        await ctx.services["notifier"].send(f"Invoice {ctx.entity_id} approved")
```

For guaranteed delivery, record an event and use `OutboxDelivery`: the event is
stored in the same transaction and a consumer does the effect.

A *staged* effect, such as a blob stored under a new key, may happen before
commit if it registers its undo: `ctx.on_rollback(lambda: store.delete([key]))`.

## Retries: idempotency keys

Writes take `idempotency_key=` (HTTP: the `Idempotency-Key` header). A retry
with the same key and request returns the first result without running again;
the record is stored in the operation's transaction, so a failed write keeps no
key. Use them for anything a client may retry: payments, actions, creates.

## Several operations in one transaction

```python
async with manager.transaction():
    await manager.update("invoices", 7, {"amount": "120"})
    await manager.transition("invoices", 7, "submit")
```

They commit together. A failure inside dooms the block: leaving it raises
RE-E007 and rolls everything back, even if your code caught the error.
