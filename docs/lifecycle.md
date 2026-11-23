# Lifecycle contract

This order is part of the public contract and is covered by tests
(`test_hook_order_is_contractual`).

## Writing operations (create, update, delete, transition, action, attach, detach)

| # | Step | Transactional | Hook points |
|---|---|---|---|
| 1 | Resolve resource and operation (RE-E002, RE-E003, RE-E202, RE-E301) | – | |
| 2 | Authorization, record unknown (RE-E702) — see [permissions](permissions.md) | – | |
| 3 | Validate input with the operation's schema (RE-E101) | no | `before_validate`, `after_validate` |
| 4 | Open the unit of work | yes | |
| 5 | Load the entity (update, delete, transition, instance action) (RE-E102); then authorization on the record (404 if invisible, else RE-E702); on create and update, every field sent must be writable (RE-E706) | yes | |
| 6 | Before mutation | yes | `before_create` · `before_update` · `before_delete` · `before_transition[:event]` · `before_action[:name]` · `before_attach[:slot]` · `before_detach[:slot]` |
| 7 | Mutate: build+`add` / `apply_update`+`update` / `delete` / fire the state machine+`update` / `Action.execute` / store blob + `Document` row | yes | |
| 8 | After mutation | yes | `after_create` · `after_update` · `after_delete` · `after_transition[:event]` · `after_action[:name]` · `after_attach[:slot]` · `after_detach[:slot]` |
| 9 | Build the result (read schema, without the fields the caller may not read), stage events (outbox), commit | yes | |
| 10 | Post-commit | no | `ctx.on_commit` callbacks, `after_commit` hooks, then event delivery |

Before a transition's hooks (step 6), slots with `required_for` that event need an
available document (RE-E606); deleting an entity discards its documents in step 7.
See [documents](documents.md).

On create, a resource with a state machine gets its initial state before step 6,
and the built entity is authorized then. `list` applies the caller's row filter,
refuses filters and sorting on fields the caller may not read (RE-E707) and omits
hidden fields row by row. Hooks, events and repositories always see full records.

Any exception in steps 3–9 aborts the operation; from step 4 on it also rolls the
transaction back, and no events are published. Exceptions raised by hooks get a
note naming the hook and lifecycle point.

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
undo the committed operation. Guaranteed delivery (Outbox) is planned.

## Protected state

The state field (`state_field`, default `status`) cannot appear in `create` or
`update` schemas (RE-E004) and `apply_update` ignores it: state changes only
through transitions.
