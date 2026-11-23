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

## Not yet

Retention and purge jobs, tamper evidence (hash chaining), and external sinks
(OpenTelemetry logs, SIEM).
