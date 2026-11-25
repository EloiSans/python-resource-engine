# Idempotency keys

A client that loses the answer to a write, through a timeout or a dropped
connection, can't know whether the write happened. Sending it again might
create a second invoice, or apply a discount twice. With an idempotency key,
it can retry safely: the first request runs, and every retry returns that
first result without running again.

```bash
curl -X POST /invoices -H 'Idempotency-Key: 3f2c9a1e-order-7' -d '{"customer": "ACME", "amount": "75"}'
```

```python
await manager.create("invoices", data, actor=user, idempotency_key="3f2c9a1e-order-7")
```

`create`, `update`, `delete`, `transition` and `run_action` accept the key.
Every generated write route accepts the `Idempotency-Key` header: `POST`,
`PATCH` and `DELETE` on records, transitions and actions.

## What happens

| Request | Result |
|---|---|
| A key not seen before | The operation runs. Its result is stored **in the same transaction** as the change |
| The same caller, key and request again | The stored result comes back, with the same status and body. Nothing runs, nothing is published, nothing is audited again |
| The same caller and key, a different request | Refused: RE-E107 (422) |
| The same key from another caller | A separate key: keys are per caller |
| The first request failed | Nothing was stored, because the key rolled back with the change, so a retry runs for real |
| Two requests with one key at the same time | Both run, but only one can commit the key. The other rolls back and returns the winner's result |
| The key is older than `idempotency_ttl` (24 hours by default) | Treated as new |

- **What counts as the same request:** the operation, the resource, the
  record, the transition or action, the input as sent, `expected_version` and
  `tenant`.
- **Who the caller is:** `Principal.id`, `system:<job>` for engine jobs, or
  `anonymous`.
- **Keys:** 1 to 255 printable ASCII characters. A UUID per user action is a
  good choice.
- **Hidden fields** stay hidden on a replay, as in the first response.

## Setup

The record lives in the operation's unit of work. The in-memory one has it
already. With SQLAlchemy, add the table:

```python
engine = engine_tables(metadata, idempotency=True, ...)
uow = unit_of_work_factory(sessions, registry, idempotency_table=engine.idempotency)
manager = ResourceManager(registry, uow, idempotency_ttl=timedelta(hours=24))
```

Without a table, sending a key fails with RE-E008. Writes without a key never
need it.

Expired records are deleted by `IdempotencyJanitor`. Run it periodically:

```python
from resource_engine import IdempotencyJanitor

runner.add("idempotency", IdempotencyJanitor(uow_factory), every=3600)
```

## Limits

- **Inside `manager.transaction()`:** a key is checked and stored like any
  other write. A race with another request is only detected when the whole
  transaction commits. Then that commit fails, and a retry of the block
  replays.
- **Document uploads** (`attach`, `request_upload`, `confirm_upload`) don't
  take keys yet.
- **Calls a hook or an action makes to other services** are not stored or
  replayed. Only the operation's result is. Keep those calls after commit, as
  usual.
