# Anti-patterns

Each entry: what goes wrong, the wrong code, the fix. Check your change against
this list before finishing.

## 1. An e-mail or HTTP call inside the transaction

The call happens even if the operation then rolls back, and slows the
transaction down. `check` reports it (RE-C002).

```python
# Wrong
def notify(ctx):
    httpx.post("https://crm.example/invoices", json={"id": ctx.entity.id})


hooks = {"after_create": [notify]}
```

```python
# Right: after commit, marked
@external_effect
async def notify(ctx):
    """Avisa el CRM d'una factura nova.

    Effects:
        Crida l'API del CRM.
    """
    await ctx.services["crm"].invoice_created(ctx.entity_id)


hooks = {"after_commit": [notify]}


# Right, with guaranteed delivery: an event handled by a consumer
def record(ctx):
    ctx.record_event(InvoiceIssued(invoice_id=ctx.entity.id))
```

## 2. Changing the state outside a transition

```python
# Wrong
class InvoiceUpdate(BaseModel):
    status: str | None = None  # RE-E004 at registration


def approve_big_ones(ctx):
    ctx.entity.status = "approved"  # bypasses permissions, hooks and events
```

```python
# Right
await manager.transition("invoices", invoice_id, "approve", actor=user)
```

## 3. Permission checks in hooks

```python
# Wrong
def only_managers(ctx):
    if "manager" not in ctx.actor.roles:
        raise DomainError("forbidden")
```

```python
# Right: a policy (data), or a system role in code
{"effect": "allow", "subjects": {"roles": ["manager"]}, "actions": ["invoices:transition:approve"]}
```

Policies are audited, explained (`/authz/explain`), turned into SQL filters
for `list`, and editable without a deploy. Hook checks are none of that.

## 4. Business rules in hooks instead of the domain

```python
# Wrong
def check_amount(ctx):
    if ctx.entity.amount < 0:
        raise ValueError("negative")
```

```python
# Right: the rule is a domain method; the hook only calls it
class Invoice:
    def ensure_valid_amount(self) -> None:
        if self.amount < 0:
            raise InvoiceError("Invoice amount cannot be negative")


def validate_amount(ctx):
    """Rebutja factures amb import negatiu."""
    ctx.entity.ensure_valid_amount()
```

Raise a `DomainError` subclass (HTTP 422 with your code), not `ValueError`.

## 5. A business process written as a hook chain

```python
# Wrong: approving creates a payment from inside a hook
def create_payment(ctx):
    ctx.repository("payments").add(Payment(invoice_id=ctx.entity.id))


hooks = {"after_transition:approve": [create_payment]}
```

```python
# Right: an action (or a consumer of TransitionExecuted) that the caller runs
class SchedulePayment(Action):
    """Programa el pagament d'una factura aprovada.

    Postconditions:
        Hi ha un pagament pendent per a la factura.
    """

    async def execute(self, ctx):
        await ctx.repository("payments").add(Payment(invoice_id=ctx.entity.id))
```

## 6. Framework imports in the domain

```python
# Wrong (domain/entities.py)
from pydantic import BaseModel
from sqlalchemy.orm import Mapped
```

```python
# Right: plain dataclasses in domain/; Pydantic in application/schemas.py;
# SQLAlchemy imperative mapping in infrastructure/
@dataclass
class Invoice:
    customer: str
    amount: Decimal
    status: str | None = None
    id: int | None = None
```

`check` reports it (RE-C001).

## 7. Doing transaction work after commit

```python
# Wrong (after_commit)
def stamp(ctx):
    ctx.entity.sent_at = datetime.now(UTC)  # never saved
    ctx.record_event(InvoiceSent(...))  # lost with the outbox
```

```python
# Right: before commit
def stamp(ctx):
    ctx.entity.sent_at = datetime.now(UTC)
    ctx.record_event(InvoiceSent(invoice_id=ctx.entity.id))


hooks = {"after_transition:send": [stamp]}
```

`check` reports it (RE-C003).

## 8. Writing the database directly

```python
# Wrong: skips validation, permissions, hooks, audit and events
async with session_factory() as session:
    await session.execute(update(invoices_table).values(amount=0))
```

```python
# Right
await manager.update("invoices", invoice_id, {"amount": "0"}, actor=user)
# several operations together
async with manager.transaction():
    ...
```

Jobs and consumers use `system_principal("job-name")` as the actor.

## 9. Tenant filters by hand

```python
# Wrong
query = ListQuery(filters={"org": user.tenant})
```

```python
# Right: declare it once; the engine confines every operation
class LeadResource(Resource):
    tenant_field = "org"
```

## 10. Undocumented elements

A resource, enabled operation, state, transition or action without a
docstring fails `resource-engine docs validate --strict`. Write the docstring
with the change, not afterwards ([docstrings.md](docstrings.md)).

## 11. Testing with mocks of the engine

Don't mock `ResourceManager`, repositories or hooks. Use the real manager on
`InMemoryStore` ([testing.md](testing.md)); it runs the whole lifecycle in
milliseconds.
