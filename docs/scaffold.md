# Scaffolding

`resource-engine scaffold` writes the structural code of a module and of every
part of one (actions, state machines, hooks, domain events, document slots,
references and seed policies), laid out like the reference module
(`examples/invoices`). The same input always gives the same files. You or your
agent then write only the business logic and the descriptions. The same
commands are tools of the [development MCP server](ai.md).

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
`ApplyDiscountInput` schema goes into `application/schemas.py`. The action is
added to the resource's `actions`. The command prints the permission
(`orders:action:apply_discount`) and the route.

## A state machine

```bash
resource-engine scaffold state-machine Order --package shop.orders --states new,paid --transitions 'pay:new>paid'
```

It writes `domain/state_machine.py` and lists what to change by hand: the
resource, the entity's `status` field, the read schema, the table and the
grants.

## Parts of a module

Each of these writes its code and declares it on the resource class (found in
`application/`, by `name` or by the default name), so the module keeps
importing, registering and passing `check`:

```bash
resource-engine scaffold hook orders check_credit --package shop.orders \
  --on before_create --on before_transition:place
resource-engine scaffold hook orders tell_warehouse --package shop.orders --on after_commit --effect
resource-engine scaffold event orders OrderShipped --package shop.orders --field carrier:str
resource-engine scaffold slot orders delivery_note --package shop.orders \
  --content-type application/pdf --max-mb 5 --required-for ship
resource-engine scaffold reference orders customer_id --package shop.orders --to customers
resource-engine scaffold policy orders --package shop.orders --role clerk
resource-engine scaffold policy orders --package shop.orders --role auditor --read-only
```

| Part | Writes | Declares on the resource |
|---|---|---|
| `hook` | a function in `application/hooks.py` | `hooks`, at every `--on` point |
| `event` | a `DomainEvent` dataclass in `domain/events.py` (with `<singular>_id`) | `events` |
| `slot` | — | a `DocumentSlot` in `documents` |
| `reference` | the id field in the entity, the schemas and the table, when missing | a `Reference` in `references` |
| `policy` | a seed policy in `<package>/authz.py` (`SEED_POLICIES`) | — |

- **Hooks:** `--on` takes any hook point, with a transition or action
  qualifier where the point accepts one (`before_transition:place`). Hooks
  are plain functions, except `after_commit` ones, which are async. With
  `--effect` (after `after_commit` only) the hook is marked
  `@external_effect` and documents its effects. An effect inside the
  transaction is refused here, as `check` would report it (RE-C002).
- **Events:** record them where they happen with `ctx.record_event(...)`.
  Declared events are listed in the docs and the AsyncAPI document.
- **References:** the relation is named after the field without `_id`, or
  `<name>_record` when the entity already has a field with that name.
  `--on-delete set_null` needs an optional field. The referenced resource
  must be registered too, the module's own tests included.
- **Policies:** the actions come from the registered resource: its
  operations, transitions, actions and document slots, or with `--read-only`
  `list`, `get` and downloads. Point `check` at the list
  (`[tool.resource-engine] policies = "shop.orders.authz:SEED_POLICIES"`)
  and create the policies at start-up.
- **Edits:** they add lines and keep comments and layout. A one-line list or
  dict that grows is rewritten on several lines. Names that are already
  there (a function, an event class, a slot, a reference, a policy name)
  are refused.

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
