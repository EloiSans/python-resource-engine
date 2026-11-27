# Composite stores

One operation can touch resources held in different places: invoices in
PostgreSQL, partners in the ERP's REST API, daily sales in the data lake. A
`CompositeStore` gives the manager one unit of work that sends each resource
to its own store.

```python
from resource_engine.composite import CompositeStore
from resource_engine.querying import QueryEngineStore
from resource_engine_http import rest_unit_of_work_factory

erp = rest_unit_of_work_factory(http, {"partners": partners_ep, "erp_orders": orders_ep})
lake = QueryEngineStore(trino, registry, tables={"daily_sales": "lake.sales.daily"})

store = CompositeStore(
    sql.unit_of_work,  # the primary store
    {
        "partners": erp,
        "erp_orders": erp,
        "daily_sales": lake.unit_of_work,
    },
    compensations={"erp_orders": {"add": cancel_order}},
    registry=registry,
)
manager = ResourceManager(registry, store.unit_of_work)
```

## Routing

- Resources listed in the mapping go to their store. Resources sharing a
  factory share one unit of work per operation.
- Everything else goes to the **primary store**, which also keeps the outbox,
  the inbox, the idempotency records and the audit entries. Events and the
  audit therefore commit with the SQL writes.
- `manager.transaction()` works across stores: the operations inside it share
  the composite unit of work.

## Remote writes and compensations

A remote API can't join the database transaction: its writes take effect when
they are made. Stores whose unit of work says `transactional = False` (the
REST, SOAP and OData stores) have their repositories wrapped, and every write
records its compensation:

| Write | Default compensation |
|---|---|
| create | delete the created record |
| update | update back to the record as it was (read before the write) |
| delete | create the record again |

The compensations run in reverse order when:

- the operation, or a later operation in the same `manager.transaction()`,
  fails;
- the primary commit fails after the remote writes went through.

When the operation commits, the log is dropped.

### Your own compensations

When "delete" is the wrong inverse (an order must be cancelled, a payment
refunded), give a function per resource and write kind. It receives the
repository, the record before and the record after:

```python
async def cancel_order(repository, before, after):
    await erp_client.post(f"/orders/{after.id}/cancel")
```

### Best effort

Compensations are best effort, not a distributed transaction. A failing
compensation is logged, reported through the `ErrorReporter`
(`where="composite.compensation"`) and the others still run. Keep remote
writes few and late in the operation, and prefer remote APIs with idempotent
writes. For processes spanning long times or several services, use events and
workflows instead.

## Other stores

- **Transactional stores** (another database) commit before the primary and
  roll back with it. That is not two-phase commit: if the primary commit fails
  after another database committed, the other database keeps its writes.
- **Read-only stores** (query engines) just serve reads.
