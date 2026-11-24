# Audit trail

Who changed what, when, and who was refused. With an audit sink, the engine
writes one entry per operation:

- **committed changes** (create, update, delete, transitions, actions, document
  attach/detach/processing), in the **same transaction** as the change. If the
  change rolls back, so does its entry;
- **refused attempts** (RE-E702, RE-E706, RE-E707, and records hidden with a 404),
  written on their own, because the operation's transaction rolls back;
- **reads** (`get`, `list`) only for resources with `audit_reads = True`.

## Setup

```python
registry.register_audit()  # the built-in, read-only audit_log resource
manager = ResourceManager(registry, uow_factory, authorizer=..., audit=True)
```

`audit=True` is short for `RepositoryAuditSink(uow_factory)`. With SQLAlchemy,
add the table next to your own: `map_audit(mapper_registry, audit_table(metadata))`.
A `RepositoryAuditSink` without the `audit_log` resource fails at bootstrap
(RE-E001).

## Entries

| Field | Content |
|---|---|
| `id`, `at` | Entry id, UTC time |
| `actor`, `actor_roles` | `Principal.id`, `system:<job>` for engine jobs, `null` for anonymous; application roles at the time |
| `resource`, `entity_id` | Target record (the id as text) |
| `operation`, `action` | `create`, `update`, `transition`… and the permission action (`transition:approve`, `attach:signed_pdf`) |
| `outcome` | `ok` or `denied` |
| `changes` | `{field: {"from": …, "to": …}}`; masked fields are `{"changed": true}` |
| `details` | Transition or action input, document events (slot, id, type, size; never content), list query and count, denial `code`, `reason` and `fields` |
| `correlation_id` | Shared with the logs and events of the operation |

What `changes` holds per operation: `create` the values sent, `update` the
fields that changed, `delete` the last values, `transition` the state field.

## Sensitive values

Restricted fields ([permissions](permissions.md#fields)) and fields listed in
`audit_exclude` are recorded as changed, without values:

```python
class EmployeeResource(Resource):
    restricted_fields = ["salary"]
    audit_exclude = ["iban"]
    audit_reads = True  # also log get / list
    # audit = False      # leave this resource out
```

## Reading the trail

`audit_log` is an ordinary resource with `list` and `get` only, and it is
sensitive: wildcard grants (`*:list`, `viewer`) do not reach it, only `admin` and
explicit grants (`audit_log:list`). Nothing can create, change or delete entries
through the engine.

```
GET /audit_log?resource=invoices&entity_id=7&order_by=at   # a record's history
GET /audit_log?actor=keycloak:5f2…&outcome=denied           # someone's refusals
GET /audit_log?correlation_id=…                             # one request
```

## Sinks

`AuditSink` is a port (`record(entries, *, uow=None)`):

| Sink | Use |
|---|---|
| `RepositoryAuditSink` | Default: the `audit_log` resource, in the operation's transaction |
| `LoggingAuditSink` | One JSON log line per entry, for log shipping |
| `CompositeAuditSink(a, b)` | Several sinks |

Entries written on their own (refusals, reads) are best effort: a failing sink is
logged and never changes the outcome of the operation. Entries of committed
changes are part of the transaction: if writing them fails, the change fails.

## Tamper evidence

Entries are written by concurrent transactions, so they are sealed in batches
rather than chained one by one. A periodic job seals the entries written since
its last run:

```python
from resource_engine.audit import AuditSealer

registry.register_audit(seals=True)  # adds the read-only audit_seals resource
sealer = AuditSealer(uow_factory, key=settings.audit_key, key_id="2026-11")
await sealer.seal_pending()  # from a scheduled job
report = await sealer.verify()
if not report.ok:
    for problem in report.problems:
        print(problem.sequence, problem.kind, problem.message)
```

With SQLAlchemy, add the seal table:
`map_audit(mapper, audit_table(metadata), audit_seal_table(metadata))`. The
audit table also has a `seal` column: add it with a migration if your table
predates it.

Each seal covers up to `batch_size` entries (1000 by default) in `(at, id)`
order:

| Field | Content |
|---|---|
| `sequence` | Position in the chain, from 1 |
| `entries`, `first_at`, `last_at` | How many entries, and their time range |
| `digest` | SHA-256 of the entries as canonical JSON |
| `hash` | SHA-256 of the digest, the fields above and `previous` |
| `previous` | The previous seal's `hash`: seals form a chain |
| `mac`, `key_id` | HMAC-SHA-256 of `hash`, when a key is configured |
| `purged_at` | When retention deleted the batch's entries |

Each entry records its seal in `seal`, `null` until sealed.

`verify()` recomputes every seal and batch. It reports:

| Kind | Meaning |
|---|---|
| `entries_changed` | An entry was changed, deleted, or moved into or out of the batch |
| `seal_changed` | A seal's fields don't match its hash |
| `chain_broken` | `previous` doesn't match the seal before |
| `seal_missing` | A gap in the sequence |
| `bad_mac`, `unsigned`, `unknown_key` | The HMAC doesn't match, is missing, or its key is unknown |
| `orphan_entries` | Entries point at a seal that doesn't exist |

It also returns how many entries are still unsealed.

What it covers, and what it doesn't:

- **Without a key**, seals show accidental and partial edits. Someone who can
  write the database can still rewrite entries and seals together.
- **With a key** kept outside the database, rewriting the seals needs the key.
  To rotate keys, pass the earlier ones as `old_keys={"2026-05": old_key}`.
- **New entries are not covered until the next run.** Only entries older than
  `settle` (5 minutes by default) are sealed, so transactions still running
  when the job starts join a later batch instead of leaving a gap.
- **Forged entries aren't detected.** An entry inserted directly into the table
  is sealed like any other. Sealing proves later changes, not authorship.
- **Run one sealer at a time.** In SQL, two sealers racing collide on the
  seal's primary key, and one of them rolls back.

## Retention

Entries are kept forever unless you configure an age:

```python
from datetime import timedelta

from resource_engine.audit import AuditJanitor

janitor = AuditJanitor(uow_factory, retention=timedelta(days=365 * 7), sealer=sealer)
await janitor.run_once()  # from a scheduled job, or: await janitor.run_forever(interval=3600)
```

- **With a sealer**, each run seals first. Then it deletes whole sealed batches
  whose newest entry is past retention. The seal stays, marked `purged_at`, so
  the chain still verifies. Unsealed entries are never deleted.
- **Without a sealer**, entries older than `retention` are deleted directly, in
  batches of `batch_size`.
- `retention` applies to every entry, changes and refusals alike.
- `AuditJanitor(sealer=sealer)` with no retention only seals.

## Not yet

- External sinks (OpenTelemetry logs, SIEM).
- Retention per resource or per outcome.
