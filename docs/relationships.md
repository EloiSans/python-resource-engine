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

## Reverse relations

Every reference also gives the referenced resource a reverse relation, named
after the referencing resource: `customers` gets `invoices`. Pass
`Reference(..., related_name="issued_invoices")` to rename it, or
`reverse=False` to leave it out. Resources without `list` have none.

When the manager is built, it refuses a reverse name that clashes with a field,
a relation, another reverse relation, or a route word (`transitions`,
`history`, `actions`, `documents`). Two references from the same resource to
the same target therefore need distinct `related_name`s.

**A separate, paged call (the default):**

```python
page = await manager.list_related("customers", 7, "invoices", ListQuery(limit=20), actor=user)
```

```http
GET /customers/7/invoices?limit=20&offset=0&order_by=-issue_date&status=draft
```

- **The parent:** it must be readable by the caller (`get`); otherwise the
  answer is 404 or 403, as for `GET /customers/7`.
- **The records:** they are listed like `GET /invoices`, with the caller's
  `list` permissions, row filters, field permissions and tenant. The related
  resource's filters, ordering and `expand` all apply.
- **The response:** the usual page, `{items, total, offset, limit}`.
- **MCP:** the tool is `customers_invoices`.

**Inline, opt-in and capped:**

```http
GET /customers/7?expand=invoices          # first 10
GET /customers/7?expand=invoices:25       # first 25 (up to the related resource's max_page_size)
```

```json
{"id": 7, "name": "ACME", "invoices": {"items": [...], "total": 134, "limit": 10, "has_more": true}}
```

- **Embedded page:** it shows the first records in the related resource's
  default order, with the caller's permissions. `total` counts only what the
  caller can see.
- **Can't list them:** if the caller cannot list the related resource, the
  value is `null`.
- **More records:** for more than the embedded page, call the paged route
  with `offset`.
- **Cost:** on `list`, each record's page is one more query, so keep inline
  pages small, or use the paged route.

Use inline expansion for short lists that are always shown with their parent,
like an order's lines. Use the separate call for anything that can grow, like
a customer's invoices or comment threads, where the front end pages, filters
or loads it lazily.

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
