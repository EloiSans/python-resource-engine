# External policy engines

The built-in `PolicyAuthorizer` stores policies as data and lets administrators
manage them from a front end ([permissions](permissions.md)). If your
organisation already runs a policy engine, use `EngineAuthorizer` with one of
these adapters instead:

| Engine | Package | Class | Model | Row filters | Tested against |
|---|---|---|---|---|---|
| [OPA](https://www.openpolicyagent.org/) (Rego) | `resource-engine-opa` | `OpaEngine` | Attribute rules on a server | Compile API (UCAST) | OPA 1.22.4 (compose) |
| [Cedar](https://www.cedarpolicy.com/) | `resource-engine-cedar` | `CedarEngine` | Attribute rules, in process | Partial evaluation | cedarpy 4.12 |
| [OpenFGA](https://openfga.dev/) | `resource-engine-openfga` | `OpenFgaEngine` | Relationships (tuples) | `list_objects` | OpenFGA 1.22.0 (compose) |

```python
from resource_engine.authz import AuthzConfig, EngineAuthorizer, IdpRoleSource
from resource_engine_cedar import CedarEngine

config = AuthzConfig(role_source=IdpRoleSource({"acme-admins": "admin", "acme-clerks": "clerk"}))
authorizer = EngineAuthorizer(CedarEngine(Path("policies/")), config)
manager = ResourceManager(registry, uow_factory, authorizer=authorizer)
```

The policies live in the engine: Rego in OPA, `.cedar` files, or the OpenFGA
model and tuples. Do not call `registry.register_authz()`; the `authz_*`
resources belong to `PolicyAuthorizer`. `GET /authz/catalog`, `/authz/me` and
`/authz/explain` keep working.

## What stays the same

- **Actions** are the same names as with built-in policies:
  `invoices:create`, `invoices:transition:approve`, `invoices:read:notes`. The
  `actions` section of [permissions](permissions.md#actions) lists them.
- **System roles in code are decided first.** `admin` (or any `SystemRole`
  grant) is allowed without asking the engine. Admin also stays cross-tenant
  ([tenancy](tenancy.md)). Engine jobs (`system_principal`) are always allowed.
- **Lifecycle:** step 2 asks without the record, and step 5b asks again with it.
  Records the caller may not see are a 404. `list` is filtered in the repository,
  in SQL with SQLAlchemy.
- **Fields** restricted in code (`restricted_fields`) are asked as
  `<resource>:read:<field>` and `<resource>:write:<field>`. Hidden fields are
  omitted, and writes are rejected with RE-E706. Other fields are open: the
  engine is not asked about them.
- **Tenancy** applies before the engine, whatever the policies say.

## The request

Every question is an `AuthzRequest`:

| Part | Content |
|---|---|
| `subject` | `id` (`provider:subject`), `subject`, `roles` (application roles from the role source), `groups`, `scopes`, `tenant`, `email`, `claims`…; `null` for anonymous callers |
| `action` | `invoices:get`, `invoices:transition:approve`… |
| `resource_type` | Resource name (`invoices`) |
| `resource` | The record's attributes, once loaded |
| `input` | Validated input of the operation (create, update, transitions, actions) |
| `context` | `operation`, `action`, `now` |

When `resource` or `input` is not known yet, the engine answers with what is left
to decide: a *residual*. The adapter translates it into the engine's
[condition language](permissions.md#conditions):

- At step 2, a residual means "depends on the record".
- For `list`, the residual becomes the row filter.
- For fields, the residual is evaluated per record.

If a rule cannot be translated, the operation **fails closed** with RE-E705 and
names the rule. The adapter never guesses.

## OPA

```python
from resource_engine_opa import OpaEngine

engine = OpaEngine("http://opa:8181", package="resource_engine", rule="allow")
```

Decisions call `POST /v1/data/resource_engine/allow` with the request as
`input`. When something is unknown, the engine calls the compile API instead
(`POST /v1/compile/resource_engine/allow`, UCAST target), with `input.resource`
and/or `input.input` as unknowns.

```rego
package resource_engine

import rego.v1

allow if {
	input.action in {"invoices:list", "invoices:get"}
	"clerk" in input.subject.roles
	input.resource.owner == input.subject.id
	input.resource.status != "void"
}

allow if {
	input.action == "invoices:create"
	"clerk" in input.subject.roles
	input.input.amount <= 1000
}
```

Write rules the compile API can turn into filters:

- Don't use `default allow`.
- Compare the unknown parts (`input.resource.*`, `input.input.*`) with `==`,
  `!=`, `<`, `<=`, `>`, `>=`, or `x in {…}` where the set is known.
- Keep everything else on known values (`subject`, `action`, `context`).

OPA rejects other constructs with a fragment error, for example
`"urgent" in input.resource.tags`. That error becomes RE-E705. `startswith` and
`endswith` are refused too. Decimals are sent as JSON numbers and dates as ISO
text. An unreachable OPA is RE-E501, and the operation is refused.

## Cedar

```python
from resource_engine_cedar import CedarEngine

engine = CedarEngine(Path("policies/"), entity_types={"invoices": "Invoice"})
```

| Request | Cedar |
|---|---|
| subject | `User::"<id>"`; attributes as above; its roles are parents `Role::"<name>"` |
| action | `Action::"invoices:approve"` |
| record | `<Type>::"<id>"`: PascalCase of the resource (`invoice_lines` → `InvoiceLines`) or `entity_types` |
| input, operation, now | `context.input`, `context.operation`, `context.now` |

```cedar
permit(principal in Role::"clerk", action in [Action::"invoices:list", Action::"invoices:get"],
       resource is Invoice)
when { resource.owner == principal.id || resource.amount.lessThan(decimal("10.0")) };

permit(principal in Role::"clerk", action == Action::"invoices:create", resource)
when { context.input.amount.lessThanOrEqual(decimal("1000.0")) };

forbid(principal, action, resource is Invoice) when { resource.status == "void" };
```

How values are encoded:

- **Decimals** become Cedar `decimal` values, rounded to 4 places. Compare them
  with `lessThan`, `lessThanOrEqual`, `greaterThan` and `greaterThanOrEqual`;
  Cedar's `<` doesn't accept decimals.
- **Dates and datetimes** become `datetime` values.
- **`None`** attributes are left out. Test for them with `has`.

The translator supports these Cedar constructs:

- `== != < <= > >= && || !`;
- `has`;
- `contains`;
- `in` with a set;
- `is`;
- the decimal comparisons;
- attributes of the record and of `context.input`.

`like`, `in` on entity hierarchies, arithmetic, `if` and tags fail closed. Call
`engine.reload()` after editing the files.

### Validating policies against the application

A misspelled action or attribute in a Cedar policy doesn't fail: it just
never matches, and access is silently denied. A schema generated from the
registry catches those mistakes before they ship:

```bash
uv run resource-engine authz cedar-schema --entity-type invoices=Invoice --out cedar.schema.json
uv run resource-engine authz cedar-schema --entity-type invoices=Invoice --check policies/   # in CI
```

```python
from resource_engine_cedar import CedarEngine, cedar_schema, validate

schema = cedar_schema(registry, entity_types={"invoices": "Invoice"})
assert validate(Path("policies/"), schema) == []
engine = CedarEngine(Path("policies/"), schema=schema, entity_types={"invoices": "Invoice"})
```

- **Principal:** `User`, a member of `Role`, with `id`, `subject`,
  `provider`, `roles`, `groups` and `scopes`, plus the optional `tenant`,
  `client_id`, `service`, `email` and `name`. The token's other `claims` are
  left out, because their shape is the provider's.
- **Resources:** one entity type per resource, with the entity's attributes
  and their types: strings, longs, booleans, `decimal`, `datetime`, sets and
  records. Fields typed `X | None` are optional, so policies test them with
  `has`.
- **Actions:** every action of the catalogue, field actions included,
  applying to its resource's type. Their context has `action`, `now` and
  `operation`, and `input` typed from the operation's input schema.
- **Requests fit it:** with a JSON schema, `CedarEngine` sends only the
  attributes the schema declares, so requests always fit it.

## OpenFGA

```python
from resource_engine_openfga import OpenFgaEngine, TupleSync

engine = OpenFgaEngine(
    api_url="http://openfga:8080",
    store_id=STORE_ID,
    model_id=MODEL_ID,
    relations={
        "invoices:get": "viewer",
        "invoices:list": "viewer",
        "invoices:approve": "approver",
        "invoices:create": "creator",
        "invoices:read:notes": "auditor",
    },
)
```

- **Actions map to relations.** The first matching pattern wins. Unmapped
  actions are denied.
- **Objects are `<type>:<id>`.** Principals are `user:<Principal.id>`.
  OpenFGA ids can't contain `:`, so `engine.user_ref("oidc:ann")` gives
  `user:oidc|ann`.
- **Roles are sent as contextual tuples**, `user:<id> member role:<name>`.
  Models can grant through `role#member` without syncing role assignments.
- **Actions without a record** (`*:create` by default) are checked on the
  collection object, `<type>:collection`.
  Example tuple: `role:clerk#member creator invoices:collection`.
- **Row filters** call `list_objects` and become `id in [...]`. Above
  `max_objects` (1000 by default), the filter is refused rather than
  truncated. Grant broad listing through a system role instead.

Records and tuples must agree. `TupleSync` writes the tuples a record implies
when it is created, and undoes the write if the operation rolls back. It
deletes them after a delete commits:

```python
tuples = TupleSync(engine, {"invoices": lambda i: [(engine.user_ref(i.owner), "owner")]})


class InvoiceResource(Resource):
    hooks = tuples.hooks("invoices")
```

Changes that move relationships, such as a new owner, need a hook of their own.

### Conditions

OpenFGA conditions (CEL) attach a rule to a tuple: a role may create
payments *under its limit*. With `conditions=True`, every check sends the
condition context:

| Parameter | Value |
|---|---|
| `input_<field>` | The operation's validated input (`input_amount`) |
| `resource_<field>` | The record's attributes (`resource_status`) |
| `action`, `operation` | The action and the operation |
| `now` | The time of the check (RFC 3339) |

Values are scalars or lists of scalars: decimals become doubles, and dates
and datetimes become RFC 3339 text. Nested objects and `None` are left out.

```text
condition input_under_limit(input_amount: double, limit: double) {
  input_amount < limit
}

type payments
  relations
    define creator: [role#member with input_under_limit]
```

```python
engine = OpenFgaEngine(..., relations={"payments:create": "creator"}, conditions=True)
await engine.client.write_tuples(
    [
        ClientTuple(
            user="role:clerk#member",
            relation="creator",
            object="payments:collection",
            condition=RelationshipCondition(name="input_under_limit", context={"limit": 1000.0}),
        )
    ]
)
```

The input isn't known when the engine is first asked (step 2). A conditional
decision therefore waits for the record: the engine answers "decide with the
record", and the real check runs at step 5b with the input and the record.
Row filters (`list_objects`) send the same context, without the input.

## OPA bundles

```bash
uv run resource-engine authz opa-bundle --policies policies/ --out bundle.tar.gz
```

The bundle holds your `.rego` files (test files `*_test.rego` stay out) and
the application's catalogue as data at `data.resource_engine.catalog`:

- `actions`: every action with its `resource`, `kind`, `qualifier`,
  whether it's `restricted`, and its `input` attributes;
- `resources`: their `id_field`, `tenant_field`, `restricted_fields`,
  `attributes` and their types, and the `states` of each state machine;
- `system_roles`: their `grants`.

Policies can then look the catalogue up instead of repeating it:

```rego
actions := data.resource_engine.catalog.actions

allow if {
	actions[input.action].kind == "transition"
	actions[input.action].resource == "invoices"
	"approver" in input.subject.roles
}
```

Don't name a rule `catalog` in the `resource_engine` package: it would
clash with the data at that path.

- **Reproducible:** the manifest's revision is a hash of the content, so
  the same code and policies give the same bytes, and OPA only reloads when
  something changed.
- **Publishing:** build it in CI and put it where OPA's bundle service polls
  (S3, a CDN, nginx, GitHub Pages).

## Testing your engine

`resource_engine.testing.ConditionEngine` is an in-memory engine, with rules
written in the condition language. It is handy for application tests.

`resource_engine.testing.contracts.AuthorizerContract` checks any `PolicyEngine`:

- owners only;
- `list` agreeing with single checks;
- role-gated create;
- a restricted field;
- anonymous and system callers.

All three adapters run it.

## Not planned

Editing Rego or Cedar policies from the application's front end. Teams that
run OPA or Cedar keep their policies as code: in a repository, reviewed,
tested (`opa test`, Cedar's validator) and shipped through their pipeline
(OPA bundles, the Verified Permissions API), usually for several
applications at once. An editor inside one application would go around
that review. It also couldn't check a change the way the built-in editor
checks data policies: Rego is a full language, so privilege escalation and
row-filter compatibility can't be verified there.

For permissions that business users change from a front end, use the
built-in policies stored as data (`authz_policies`, see
[permissions](permissions.md)). The items above help the policy-as-code
side instead.
