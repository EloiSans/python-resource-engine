# View resources

A view is a read-only resource whose records are computed by a query: totals
per customer, invoices with their balance, a projection joining several
tables. It lists and reads like any resource, with filters, sorting, paging,
permissions, row conditions, tenancy, `expand`, generated docs, GraphQL and
MCP tools. It never writes, and nothing is stored: each read computes the
current rows.

## Aggregates

An `Aggregate` groups the records of another resource. It's neutral: every
storage adapter computes it, as one `GROUP BY` query in SQL and in Python in
memory.

```python
from resource_engine import Aggregate, Count, Max, Reference, Resource, Sum


class CustomerBalanceResource(Resource):
    name = "customer_balances"
    model = CustomerBalance  # a plain class, like any entity
    schemas = {"read": CustomerBalanceRead}  # one field per group and value
    operations = ["list", "get"]
    id_field = "customer_id"  # one of group_by: GET /customer_balances/7
    view = Aggregate(
        "invoices",
        group_by=["customer_id", "organization"],
        values={
            "invoices": Count(),
            "total": Sum("amount"),
            "outstanding": Sum("amount", where={"status": ["pending", "approved"]}),
            "paid": Sum("amount", where={"status": "paid"}),
            "last_issue_date": Max("issue_date"),
        },
    )
    references = {"customer_id": Reference("customers", name="customer", related_name="balance")}
    sortable = ["total", "outstanding"]
```

- **Values:** `Count()`, `Sum(field)` (0 when nothing matches), and `Min`,
  `Max`, `Avg` (null when nothing matches). Each takes an optional `where`,
  given as values (`{"status": "paid"}`) or lists of values. `Aggregate(where=...)`
  filters the source records first.
- **Rows:** one per distinct `group_by` value. Source records without a value
  for the id field are left out (invoices without a customer).
- **Checks:** the read schema has exactly the group and value fields, the id
  field is one of `group_by`, and the source fields exist (checked when the
  manager starts).
- **Relations:** references work for `expand` (`?expand=customer`) and give the
  target a reverse relation (`GET /customers/7/balance`). They never block
  deletes, because view rows follow their sources.

## Custom views

`view = True` declares a view whose query the storage adapter defines, for
joins, window functions or anything else SQL can compute:

```python
# SQLAlchemy: map the model onto any select, keyed by the id field
map_view(
    mapper_registry,
    InvoiceBalance,
    select(
        invoices.c.id,
        invoices.c.amount,
        (invoices.c.amount - func.coalesce(paid.c.total, 0)).label("balance"),
    ).outerjoin(paid, paid.c.invoice_id == invoices.c.id),
    primary_key="id",
)

# in memory (tests): a function of the other resources' records
store = InMemoryStore.from_registry(registry, views={"invoice_balances": balances})
```

## Storage

- **SQLAlchemy:** `map_views(mapper_registry, registry)` maps every aggregate
  view onto its `GROUP BY` query. Call it after mapping the sources. The
  generic repository then applies filters, row conditions, sorting and paging
  around the query. No database object is created, so there's no migration.
  For a large source, index the group and filter columns.
- **In memory:** `InMemoryStore.from_registry` computes aggregates from the
  unit of work's tables, uncommitted writes included.

## Permissions and tenancy

A view has its own grants (`customer_balances:list`, `customer_balances:get`)
and row conditions over its own fields. Its rows sum up source records
whatever the caller may read of them, so grant a view to the people who may
see those figures. With tenancy, the tenant field must be one of `group_by`:
each row then belongs to one tenant, and the usual tenant filter applies.

## Not supported

- Writes, state machines, actions, documents and versions on a view.
- Materialised views. Each read computes the rows, so for heavy queries map a
  custom view onto a materialised view the database refreshes.
