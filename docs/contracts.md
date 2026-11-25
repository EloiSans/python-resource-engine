# Contracts between services

Services that exchange events agree on a contract: the
[AsyncAPI document](asyncapi.md) of the provider. Resource Engine checks the
contract from both sides, so a change that would break the other service fails
in CI instead of in production.

| Side | Command or test | Fails when |
|---|---|---|
| Consumer | the generated `test_<name>_consumer.py::test_contract` (`assert_consumer_contract`) | the provider stops sending a handled event, stops sending or requiring a field the model requires, changes a type, adds enum values, or its examples no longer parse |
| Provider | `resource-engine docs asyncapi --check published.json` (`breaking_changes`) | an event or field is removed, a required field becomes optional or nullable, a type changes, or an enum gains values |

## Consumer: scaffold from the provider's document

```bash
resource-engine scaffold consumer payments --package billing.invoices \
  --asyncapi vendor/payments-asyncapi.yaml --channel payments --type-prefix com.acme.payments.
```

```text
billing/invoices/
├── consumers/payments.py                 payload models + build_router(manager)
├── tests/contracts/payments.asyncapi.json   the provider's document (snapshot, JSON)
└── tests/test_payments_consumer.py       contract test, sample payloads, handler stubs
```

**Which messages:** the messages the provider sends. In AsyncAPI 3.0 these are
the `send` operations; in 2.x, the `subscribe` operations. When neither is
marked, every channel message is used.

- `--channel` and `--message` narrow the selection.
- YAML documents need PyYAML (`pip install pyyaml`).
- Only references inside the document are followed.

**Payload models** are Pydantic models generated from the JSON Schemas:

- objects become classes, including nested and referenced ones;
- `enum` and `const` become `Literal`;
- `date-time`, `date`, `uuid` and `decimal` formats become their Python types;
- arrays become `list[...]` and maps become `dict[str, ...]`;
- names that aren't Python identifiers get an alias (`invoice-id` → `invoice_id`);
- optional fields default to `None`.

Models ignore unknown fields, so the provider can add fields without breaking
you.

**What the generator unwraps:**

- an engine envelope becomes its `payload`;
- a CloudEvent becomes its `data`, with `--type-prefix` stripped from the type;
- any other body is modelled whole, and the next steps tell you to write a
  decoder for it.

**Handlers** are registered with `payload=` (see
[typed payloads](messaging.md#typed-payloads)):

```python
def build_router(manager: ResourceManager | None = None) -> EventRouter:
    router = EventRouter(name="payments", description="TODO: ...")

    @router.on("PaymentReceived", payload=PaymentReceived)
    async def on_payment_received(envelope: EventEnvelope, event: PaymentReceived) -> None:
        """TODO: what receiving PaymentReceived does."""
        raise NotImplementedError("on_payment_received is not written yet")  # TODO

    return router
```

Until you write them, handlers raise `NotImplementedError`. Messages are then
retried and dead-lettered instead of acknowledged and lost. Write each handler
as a manager call (an action or a transition) with a service principal. Add
`"billing.invoices.consumers.payments:build_router"` to
`[tool.resource-engine] consumers` so your own AsyncAPI document lists what you
receive.

The generated code passes `ruff check` and `ruff format`, and its tests pass
as generated. A test then fails on purpose for each stub until you write it.

### The contract test

```python
def test_contract():
    document = os.environ.get("PAYMENTS_ASYNCAPI") or CONTRACT
    assert_consumer_contract(build_router(), document, channels=["payments"])
```

For each handled event, it checks that:

- the provider still sends it on those channels;
- every field the model requires is sent and required by the provider, with
  a type the model accepts (integers count as numbers; `null` only when the
  model allows it);
- the provider's enum values are ones the model knows;
- the provider's examples, and a sample built from its schema, parse.

By default the test runs against the snapshot, which is the contract you wrote
the consumer for. In CI, set `PAYMENTS_ASYNCAPI` to the provider's current
document, for example one downloaded in an earlier step, and you learn about
their breaking change before they deploy it. To accept a new provider version,
replace the snapshot and adjust the models: `scaffold consumer --force`
regenerates them, and your handlers live in the same file, so commit first and
review the diff.

## Provider: never break your consumers

Commit the document you publish (`resource-engine docs generate` writes
`asyncapi.json`, or `docs asyncapi --out`), and check every change against it:

```bash
resource-engine docs asyncapi --check docs/asyncapi.json
```

```text
BREAKING InvoiceTotalsRecalculated.new_amount: removed
BREAKING TransitionExecuted.transition: new values "archive"
```

- **Breaking:** the command exits 1 and lists the changes that break consumers
  written against the published document.
- **Compatible:** new events, new optional fields and widened types are
  allowed; it exits 0 and says the document needs publishing again.
- **Output:** `--json` gives `{"breaking": [...], "changed": bool}` for
  pipelines.

To ship a breaking change on purpose, publish it as a new event type or a new
channel, and keep the old one until consumers have moved.

## In Python

```python
from resource_engine_devtools.contracts import (
    assert_consumer_contract,
    breaking_changes,
    consumer_problems,
)

problems = consumer_problems(router, "contracts/payments.asyncapi.json", channels=["payments"])
changes = breaking_changes("published.json", current_document)  # paths or dicts
```

Each finding is a `Problem` with `event_type`, `path` (the field, dotted for
nested ones) and `message`.
