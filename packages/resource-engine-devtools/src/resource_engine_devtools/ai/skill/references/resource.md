# Declaring a resource

A `Resource` subclass is a declaration: class attributes only, validated when
registered (`ResourceRegistry.register`). Errors at registration are
`RE-E0xx` with a hint.

```python
from resource_engine import Resource

from billing.application import hooks
from billing.application.actions import RecalculateTotals
from billing.application.schemas import InvoiceCreate, InvoiceRead, InvoiceUpdate, RejectInvoice
from billing.domain.entities import Invoice
from billing.domain.state_machine import InvoiceStateMachine


class InvoiceResource(Resource):
    """Gestió de factures de l'organització.

    Business area:
        Facturació
    """

    model = Invoice                      # plain domain class, not an ORM model
    schemas = {"create": InvoiceCreate, "update": InvoiceUpdate, "read": InvoiceRead}
    operations = ["list", "get", "create", "update", "delete"]
    operation_docs = {"list": "Llista les factures.", "get": "Consulta una factura.", ...}
    state_machine = InvoiceStateMachine  # optional
    transition_schemas = {"reject": RejectInvoice}
    actions = [RecalculateTotals]
    hooks = {"before_update": [hooks.ensure_editable], "after_commit": [hooks.notify]}
    filterable = ["status", "customer"]
    sortable = ["id", "amount"]
```

## Attributes

| Attribute | Default | Meaning |
|---|---|---|
| `name` | `InvoiceResource` → `invoices` | Public name (URLs, actions, registry) |
| `model` | required | Domain entity class |
| `id_field`, `id_type` | `"id"`, `int` | Identifier attribute and type |
| `schemas` | `{}` | Pydantic models: `create`, `update`, `read` |
| `operations` | `()` | Enabled CRUD subset of `list get create update delete` |
| `operation_docs` | `{}` | One sentence per enabled CRUD operation (`docs validate` needs it) |
| `hooks` | `{}` | `{"<point>[:<qualifier>]": fn or [fn, ...]}`; see [lifecycle.md](lifecycle.md) |
| `state_machine`, `state_field` | `None`, `"status"` | See [state-machines.md](state-machines.md) |
| `transition_schemas` | `{}` | Input schema per transition event |
| `actions` | `()` | `Action` subclasses |
| `filterable`, `sortable` | `()` | Fields accepted by `list` filters and `order_by` |
| `max_page_size` | `100` | Upper bound for `list` |
| `documents` | `()` | `DocumentSlot(...)` declarations |
| `restricted_fields` | `()` | Need an explicit `<resource>:read|write:<field>` grant |
| `tenant_field` | `None` | Confine every operation to the caller's tenant |
| `version_field`, `require_version` | `None`, `False` | Optimistic concurrency (ETag / If-Match, RE-E105) |
| `authz_wildcards` | `True` | `False` for sensitive resources: wildcard grants don't reach them |
| `expose_transitions` | `True` | `False` keeps transitions internal (no HTTP routes) |
| `audit`, `audit_reads`, `audit_exclude` | `True`, `False`, `()` | Audit trail options |

## Schemas

Separate Pydantic models per role. They are the HTTP contract; the entity is
not exposed directly.

- `create`: what a client may send to create. **Never the state field, the
  id or the version field.**
- `update`: every field optional; only the fields sent are applied
  (`exclude_unset`). The id, state and version fields are ignored even if
  present.
- `read`: `model_config = ConfigDict(from_attributes=True)`; it is built from
  the entity. Include the state and version fields here.

Override `build_entity(data, ctx)`, `apply_update(entity, data, ctx)` or
`to_read(entity, ctx)` on the resource when the default mapping
(`model(**data)`, `setattr` per sent field, `read.model_validate(entity)`) is
not enough.

## Actions

Business operations that are neither CRUD nor a transition:

```python
from resource_engine import Action, OperationContext


class RecalculateTotals(Action):
    """Aplica un descompte percentual a l'import d'una factura.

    Preconditions:
        La factura és un esborrany.

    Postconditions:
        L'import queda reduït pel percentatge indicat.

    Effects:
        Emet l'esdeveniment ``InvoiceTotalsRecalculated``.
    """

    input_schema = ApplyDiscount      # or None
    # name = "recalculate_totals"     # default: snake_case of the class name
    # instance = True                 # False: collection-level (/actions/x)
    # output_schema = None            # default: the resource's read schema

    async def execute(self, ctx: OperationContext) -> None:
        invoice = ctx.entity              # loaded and authorized by the engine
        invoice.ensure_editable()         # domain rule
        invoice.apply_discount(ctx.data.percent)
        await ctx.repository().update(invoice)   # actions write themselves
        ctx.record_event(InvoiceTotalsRecalculated(invoice_id=invoice.id, ...))
```

`execute` runs inside the transaction (no external effects there). HTTP:
`POST /invoices/{id}/actions/recalculate_totals`. Permission action:
`invoices:action:recalculate_totals`.

## Events

Domain events are frozen keyword-only dataclasses in `domain/events.py`:

```python
from dataclasses import dataclass

from resource_engine.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class InvoiceTotalsRecalculated(DomainEvent):
    """L'import d'una factura s'ha recalculat."""

    invoice_id: int
    new_amount: Decimal
```

Declare them on the resource (`events = [InvoiceTotalsRecalculated]`) and record
them with `ctx.record_event(...)` in hooks before commit or in actions; `check`
reports undeclared ones (RE-C006), and the generated docs and AsyncAPI document
list the declared ones. They are published only if the transaction commits. The engine also publishes
`ResourceCreated`, `ResourceUpdated`, `ResourceDeleted`, `TransitionExecuted` and
`ActionExecuted` on its own. With `OutboxDelivery` they are stored with the
change and sent by `OutboxRelay`; consumers use `EventRouter` and
`MessageProcessor(manager=...)` for exactly-once effects. Give consumed events a
payload model (`router.on("PaymentReceived", payload=PaymentReceived)`): the
handler gets `(envelope, payment)`, invalid payloads are dead-lettered, and the
AsyncAPI document shows the schema. For another service's events, start with
`resource-engine scaffold consumer <name> --package ... --asyncapi <their
document>`: it writes the models, the router and contract tests; keep the
generated `test_contract` passing.

## Documents

```python
from resource_engine import DocumentSlot

documents = [
    DocumentSlot(
        "signed_pdf",
        content_types=["application/pdf"],
        required_for=["approve"],
        description="Factura signada pel client; cal per aprovar-la.",
    ),
    DocumentSlot("attachments", multiple=True, description="Justificants."),
]
```

Every slot needs a `description` (RE-D009). Inside an action,
`await ctx.documents.attach(NewDocument(slot, data, filename, content_type))`.

## Registering and wiring

```python
registry = ResourceRegistry()
registry.register(InvoiceResource)
registry.register_documents()  # if any resource has document slots
registry.register_authz()  # built-in policies managed from a front end
registry.register_audit()  # audit trail

manager = ResourceManager(
    registry,
    unit_of_work_factory(session_factory, registry),
    authorizer=...,
    audit=True,
    services={"notifier": notifier},
)
mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))
```

`services` are reachable from hooks and actions as `ctx.services["notifier"]`.
Declare the engine's tables on the same `MetaData` as yours
(`engine_tables(metadata, outbox=True, audit=True, ...)` with
`MetaData(naming_convention=NAMING_CONVENTION)`) and create migrations with
`alembic revision --autogenerate`; never `create_all` in production.
Map the entity with SQLAlchemy **imperative mapping** in `infrastructure/`
(`mapper_registry.map_imperatively(Invoice, invoices_table)`), never with
declarative models in the domain.

Generated HTTP API: `GET/POST /invoices`, `GET/PATCH/DELETE /invoices/{id}`,
`GET /invoices/{id}/transitions`, `POST /invoices/{id}/transitions/{event}`,
`POST /invoices/{id}/actions/{name}`.

References to other resources: `references = {"customer_id":
Reference("customers", name="customer_record")}` (`on_delete="restrict"` by
default, or `"set_null"`, `"cascade"`). Writes check the referenced record
exists and is readable (RE-E108); deleting a referenced record is refused
(RE-E109); `get`/`list` take `expand=["customer_record"]` (`?expand=` over HTTP)
and `list` filters on `customer_record.<filterable field>`. Never check
references in hooks or join by hand. See docs/relationships.md.

MCP tools for AI agents (`resource-engine-mcp`): `ResourceMcpServer(registry,
manager)` turns the same operations into tools (`invoices_list`,
`invoices_transition_approve`, `invoices_action_recalculate_totals`...) called
through the manager as the caller; `mcp.mount(app, authenticator=...)` after
`mount_resources`, or `run_stdio(actor=...)`. Tool descriptions come from
`operation_docs`, action descriptions and the resource and state machine
docstrings, so write them for a reader who only sees the tool. See docs/mcp.md.
