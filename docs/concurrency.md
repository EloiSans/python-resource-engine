# Optimistic concurrency

Two people open the same invoice and both save. Without a version, the second
save overwrites the first without warning. With a version field, the engine
refuses the second save with **409** (RE-E105), and the client reloads and
tries again. Nothing is locked while people are editing.

## Declaring it

```python
@dataclass
class Invoice:
    ...
    version: int | None = None


class InvoiceRead(BaseModel):
    ...
    version: int


class InvoiceResource(Resource):
    version_field = "version"
    # require_version = True   # refuse writes that do not send a version (428)
```

The field must be in the read schema and not in the create schema. It can't be
the id, state or tenant field. New records start at version 1. Every update,
transition, instance action and delete checks the version, and the writes add 1
to it. Attaching or detaching documents leaves it alone.

With SQLAlchemy, add a column:

```python
Column("version", Integer, nullable=False, default=1)
```

## Sending the version

| Where | How |
|---|---|
| HTTP | Responses carry `ETag: "3"`; writes send `If-Match: "3"` (`W/"3"` too; `*` skips the check) |
| Python | `manager.update(..., expected_version=3)`, same for `transition`, `run_action`, `delete` |
| Body | When the update (or transition/action input) schema has the version field, its value is the expected version; it is never written |

```
GET   /invoices/7                                   → ETag: "3"
PATCH /invoices/7  If-Match: "3"  {"amount": 110}    → 200, ETag: "4"
PATCH /invoices/7  If-Match: "3"  {"amount": 90}     → 409 RE-E105 {"expected": 3, "current": 4}
```

A malformed `If-Match` is a 400 (RE-E103). Browsers only let front ends read
`ETag` when CORS exposes it: `CORSMiddleware(..., expose_headers=["ETag"])`.

## Writes without a version

By default they are allowed, and still safe against races. The engine writes
with *compare and set*: the row must still have the version that was loaded
when the write reaches the database. If two requests load version 3 at the
same time, one of them wins. The other fails with RE-E105 and its transaction
rolls back.

What a missing version can't catch is a change the client never saw: a form
opened an hour ago and saved without `If-Match`. Use `require_version = True`
to refuse those writes with **428** (RE-E106).

## Adapters

| Repository | Support |
|---|---|
| SQLAlchemy | `UPDATE … WHERE id = :id AND version = :expected` (and `DELETE …`): one atomic statement; a concurrent transaction holding the row makes it wait, then fail. Tested on SQLite |
| In-memory | Checked against the committed store on write and again on commit |
| REST / SOAP | Not supported: the remote API owns its concurrency. A versioned resource on them fails with RE-E005 |

Repositories declare `supports_versions = True` and accept `expected_version`
in `update` and `delete` ([repository port](architecture.md#ports)).

SQL deletes with a version are issued as a single `DELETE` statement, so ORM
cascades configured on the mapping do not run. Use database `ON DELETE`
constraints instead.

## Audit and events

A refused write is written to the [audit trail](audit.md) as `denied`, with code
`RE-E105` and the `expected` and `current` versions. The version field never
appears in `changes` or in `ResourceUpdated` events.
