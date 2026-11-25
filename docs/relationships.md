# Relationships

A resource declares the fields that hold ids of other resources' records:

```python
from resource_engine import Reference, Resource


class InvoiceResource(Resource):
    references = {
        "customer_id": Reference(
            "customers",
            name="customer_record",  # default: the field without _id ("customer")
            on_delete="restrict",  # or "set_null", "cascade"
            description="Registered customer the invoice is issued to.",
        )
    }
```

The field is an ordinary field of the entity and its schemas (`customer_id: int |
None`). The engine adds four things around it:

- integrity on writes;
- a rule for deletes;
- expansion on reads;
- filters on the related record's fields.

References work across stores: the related records are read through their own
repository in the same unit of work.

When the manager is built, it refuses these declarations:

- a target that is not registered;
- a relation name that clashes with a field;
- `set_null` on a field the dependent resource cannot update to `None`;
- `cascade` on a resource without `delete`;
- cascades that form a cycle.

## Integrity

`create` and `update` check every reference set or changed by the operation.
The referenced record must exist and the caller must be able to read it
(`<target>:get`, tenancy included). Otherwise the operation fails with
**RE-E108** `ReferenceNotFound` (422), and `details` gives the `field`,
`resource` and `id`. A record the caller cannot read gets the same answer as a
missing one, so its existence does not leak.

The check runs after the `before_create` and `before_update` hooks, which may
set the reference. `None` is always accepted; make the field required in the
schema if it must be set. A database foreign key (as in the example) is a
useful backstop, but the engine does not need it.

## Deleting

Deleting a record looks at the references pointing to it:

| `on_delete` | Effect |
|---|---|
| `restrict` (default) | Refused with **RE-E109** `ReferenceInUse` (409) while any record references it. The check ignores the caller's permissions: integrity is global. |
| `set_null` | Each dependent record is updated with the field set to `None`, through `manager.update` as the same caller. |
| `cascade` | Each dependent record is deleted through `manager.delete` as the same caller. That runs its own references too, so cascades can chain. |

`set_null` and `cascade` run in the same transaction as the delete, with hooks,
permissions, audit entries and events for every record. If any of them fails,
for example because the caller may not delete a dependent, nothing is deleted.

## Expanding

`get` and `list` take `expand` with relation names:

```python
invoice = await manager.get("invoices", 7, actor=user, expand=["customer_record"])
page = await manager.list("invoices", actor=user, expand=("customer_record",))
```

Over HTTP and MCP:

```http
GET /invoices/7?expand=customer_record
GET /invoices?expand=customer_record&limit=20
```

```json
{"id": 7, "customer_id": 3, "...": "...", "customer_record": {"id": 3, "name": "ACME", "country": "ES"}}
```

- **Batch loading:** a list page loads its related records in one query (an
  `in` condition) on repositories that support conditions, and one `get` per
  id otherwise.
- **Permissions:** each related record is checked with the caller's `get`
  permission, tenant and field permissions.
- **Unreadable records:** a record the caller may not read is `null`. The id
  stays in the referencing field, and the rest of the response is served.
- **In Python:** the manager returns the usual read models, with the related
  records attached. Use `resource_engine.application.relations.expansions(model)`
  to read them, or `dump_read(model)` for the JSON form the adapters send.
- **Unknown relations** are refused with RE-E103.

## Filtering

`list` filters on the related resource's `filterable` fields with
`<relation>.<field>`:

```python
await manager.list("invoices", ListQuery(filters={"customer_record.country": "FR"}))
```

```http
GET /invoices?customer_record.country=FR
```

- **How it works:** the engine lists the matching related records with the
  caller's permissions, then filters by their ids.
- **Repositories:** this needs a repository that supports conditions
  (in-memory, SQLAlchemy). Others raise RE-E705, and you filter by the
  reference id (`customer_id`) instead, which works everywhere when it is
  `filterable`.
- **Limits:**
  - Only one level of relation is supported.
  - A related filter may match at most 1,000 records
    (`MAX_RELATED_MATCHES`); beyond that it is refused with RE-E103 and a hint
    to narrow it.
  - Fields the caller may not read cannot be used (RE-E707).

## In the generated docs and tools

- **Functional docs:** each resource page lists its references and what deleting
  does, and the referenced resource says who points to it.
- **AI context:** it carries a `references:` line.
- **OpenAPI:** it documents `expand` and the `<relation>.<field>` query
  parameters.
- **MCP:** the [MCP tools](mcp.md) take `expand` and the same filters.
