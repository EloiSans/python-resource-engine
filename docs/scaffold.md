# Scaffolding

`resource-engine scaffold` writes the structural code of a module, an action or
a state machine, laid out like the reference module (`examples/invoices`). The
same input always gives the same files. You or your agent then write only the
business logic and the descriptions.

## A module

```bash
resource-engine scaffold resource orders --package shop.orders \
  --field customer:str --field total:Decimal --field 'due:date?' --field 'notes:text?' \
  --states draft,placed,shipped,cancelled \
  --transitions 'place:draft>placed,ship:placed>shipped,cancel:draft>cancelled,cancel:placed>cancelled' \
  --versioned
```

```text
shop/orders/
├── domain/entities.py          Order dataclass, OrderError, ensure_valid()
├── domain/state_machine.py     OrderStateMachine (with --state-machine, --states or --transitions)
├── application/schemas.py      OrderCreate, OrderUpdate, OrderRead
├── application/hooks.py        validate → ctx.entity.ensure_valid()
├── application/resource.py     OrderResource: CRUD, hooks, state machine, version field
├── infrastructure/persistence.py   table and imperative mapping (map_entities)
└── tests/test_orders.py        CRUD round trip and the first transition, on InMemoryStore
```

- **Fields:** `name:type`, with `?` for optional ones. The types are `str`,
  `text`, `int`, `bool`, `Decimal`, `date` and `datetime`. `id`, `status` (with
  a state machine) and `version` (with `--versioned`) are generated for you.
- **Defaults:** `--states` defaults to `draft,active,closed`, and
  `--transitions` to one transition per step (`activate`, `close`). A state
  with no outgoing transition is final.
- **Package:** `--package` is the dotted package. `--root` is the directory
  it goes in, by default the first entry of `paths` in `[tool.resource-engine]`.

As generated, the module imports, registers, is formatted and lint-clean, and
passes `resource-engine check` and its own tests. Its docstrings start with
`TODO:`, and `docs validate` warns about each one (**RE-D010**). With
`--strict`, CI fails until they describe the business.

The command ends with the next steps:

1. Register the resource in the composition root.
2. Call `map_entities()` at start-up and add the table to your migrations.
3. Grant every operation, with a seed policy or a system role; `check`
   reports the rest as RE-C004.
4. Replace the TODOs.

## An action

```bash
resource-engine scaffold action orders ApplyDiscount --package shop.orders --input percent:Decimal
resource-engine scaffold action orders Export --package shop.orders --collection
```

The action class goes into `application/actions.py`, and with `--input` its
`ApplyDiscountInput` schema goes into `application/schemas.py`. The command
prints the line to add to the resource's `actions`, the permission
(`orders:action:apply_discount`) and the route.

## A state machine

```bash
resource-engine scaffold state-machine Order --package shop.orders --states new,paid --transitions 'pay:new>paid'
```

It writes `domain/state_machine.py` and lists what to change by hand: the
resource, the entity's `status` field, the read schema, the table and the
grants.

## Safety

- **No overwrites:** existing files are never overwritten. A module that
  already exists is refused; `--force` replaces the files.
- **Actions:** new actions are only added; an existing class name is refused.
- **Dry run:** `--dry-run` lists what would be written, and `--json` gives the
  same as data.

## A consumer

```bash
resource-engine scaffold consumer payments --package billing.invoices \
  --asyncapi vendor/payments-asyncapi.json --channel payments
```

From another service's AsyncAPI document, this writes payload models, an
`EventRouter` with one typed handler stub per message, a snapshot of the
document, and contract tests. See [contracts](contracts.md).
