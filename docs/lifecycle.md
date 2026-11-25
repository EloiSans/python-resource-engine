# Lifecycle contract

This order is part of the public contract and is covered by tests
(`test_hook_order_is_contractual`).

## Writing operations (create, update, delete, transition, action, attach, detach)

| # | Step | Transactional | Hook points |
|---|---|---|---|
| 1 | Resolve resource and operation (RE-E002, RE-E003, RE-E202, RE-E301) | – | |
| 2 | Tenant scope for tenant-scoped resources (RE-E708, RE-E709) — see [tenancy](tenancy.md); authorization, record unknown (RE-E702) — see [permissions](permissions.md) | – | |
| 3 | Validate input with the operation's schema (RE-E101) | no | `before_validate`, `after_validate` |
| 4 | Open the unit of work | yes | |
| 5 | Load the entity (update, delete, transition, instance action) (RE-E102; other tenants' records too); then authorization on the record (404 if invisible, else RE-E702); on create and update, every field sent must be writable (RE-E706); versioned resources check the version the client sent (RE-E105, RE-E106) — see [concurrency](concurrency.md) | yes | |
| 6 | Before mutation | yes | `before_create` · `before_update` · `before_delete` · `before_transition[:event]` · `before_action[:name]` · `before_attach[:slot]` · `before_detach[:slot]` |
| 7 | Mutate: build+`add` / `apply_update`+`update` / `delete` / fire the state machine+`update` / `Action.execute` / store blob + `Document` row; versioned writes compare and set the version (instance actions claim it before `execute`) | yes | |
| 8 | After mutation | yes | `after_create` · `after_update` · `after_delete` · `after_transition[:event]` · `after_action[:name]` · `after_attach[:slot]` · `after_detach[:slot]` |
| 9 | Build the result (read schema, without the fields the caller may not read), store it under the idempotency key if one was sent ([idempotency](idempotency.md)), write the audit entry, stage events (outbox), commit | yes | |
| 10 | Post-commit | no | `ctx.on_commit` callbacks, `after_commit` hooks, then event delivery |

Before a transition's hooks (step 6), slots with `required_for` that event need an
available document (RE-E606); deleting an entity discards its documents in step 7.
See [documents](documents.md).

On create, a resource with a state machine gets its initial state before step 6,
and the built entity is authorized then. `list` applies the caller's row filter,
refuses filters and sorting on fields the caller may not read (RE-E707) and omits
hidden fields row by row. Hooks, events and repositories always see full records.

Refused attempts (steps 2 and 5) are written to the audit trail on their own,
since their transaction rolls back; see [audit](audit.md).

Any exception in steps 3–9 aborts the operation; from step 4 on it also rolls the
transaction back, and no events are published. Exceptions raised by hooks get a
note naming the hook and lifecycle point.

## Grouping operations

```python
async with manager.transaction():
    await manager.update("invoices", 7, {"amount": "120"})
    await manager.transition("invoices", 7, "submit")
```

Operations inside `manager.transaction()` share one unit of work and commit
together:

- **Inside the transaction:** steps 4 to 9 run as usual, including the audit
  entries and outbox events. Reads see the uncommitted writes. Nested calls
  join the outer transaction. Another manager keeps its own.
- **After the commit:** step 10 runs for every operation, in order:
  `ctx.on_commit`, `after_commit` hooks, and direct event delivery.
- **On rollback:** every operation's `ctx.on_rollback` compensations run.
- **A failed operation dooms the transaction.** An operation that fails once
  it has reached step 4 may have written part of its changes, even if your
  code catches the error. Leaving the block then raises RE-E007 and rolls
  everything back. Failures before step 4 (validation, permission without the
  record, tenancy) and failed reads don't doom it.

Message consumers use it for [exactly-once effects](messaging.md#exactly-once-effects).

## Hook points

Hooks are `fn(ctx: OperationContext) -> None` or `async def`. Generic hooks run
first, then qualified ones (`before_transition:approve`). Unknown points or
qualifiers are rejected at registration (RE-E001).

## Staged effects

Some writes outside the database are safe *before* commit because nothing refers
to them until the commit makes them visible: a blob stored under a fresh key, a
remote record that can be deleted again. Register a compensation for them:

```python
ctx.on_rollback(lambda: store.delete([key]))  # runs if steps 4-9 fail (reverse order)
ctx.on_commit(lambda: purge(old_key))  # runs after commit, before after_commit hooks
```

Both are best effort (errors are logged). A crash can skip them, so pair them with
a clean-up job (e.g. `DocumentJanitor`). Irreversible effects (emails, payments)
are not staged effects: keep them after commit.

## External effects

Hooks before commit must not produce irreversible external effects (emails, HTTP
calls, messages). Mark such hooks with `@external_effect`: registering them
anywhere but `after_commit` fails at bootstrap with RE-E104. Prefer recording a
domain event (`ctx.record_event`) and handling it in the publisher.

Failures in `after_commit` hooks or in the publisher are logged and do **not**
undo the committed operation. For guaranteed delivery, use the outbox
([messaging](messaging.md#delivery-modes)).

`resource-engine check` finds the mismatches the marker can't:

- **RE-C002:** a hook before commit, or an action's `execute`, calls an
  `@external_effect` function or uses a network, e-mail or cloud client
  (`httpx`, `requests`, `aiohttp`, `smtplib`, `boto3`, the Google and Azure
  SDKs, `aio_pika`, `grpc`…). Helpers defined in your application are followed
  up to three calls deep. The report shows the chain (`via notify_warehouse`)
  and the line of the call.
- **RE-C003:** an `after_commit` hook does work that needs the transaction,
  which has already ended:
  - `ctx.record_event`: with the outbox, the events were already written;
  - `ctx.repository()` and `ctx.uow`: there is no unit of work any more;
  - `ctx.on_rollback`: the compensation can never run;
  - assigning to `ctx.entity`: the change is never saved.

```
RE-C002 billing/hooks.py:14: prepare runs inside the transaction (orders after_create) and uses httpx (httpx.post) (via notify_warehouse)
    Move the effect to an after_commit hook or a consumer of a domain event, or stage it with ctx.on_rollback if it can be undone.
```

The check reads source code and resolves names through each function's
globals and closure. Calls through objects it can't resolve, such as
`ctx.services.mailer` or a parameter, aren't followed. The hook lints need the
registry (`--app`, or `registry` in `[tool.resource-engine]`). Without one,
`check` runs the import rule only.

## Protected state

The state field (`state_field`, default `status`) cannot appear in `create` or
`update` schemas (RE-E004) and `apply_update` ignores it: state changes only
through transitions.
