# Permissions

Who may do what is decided in two layers:

- **System roles**, defined in code or config (`admin`, `viewer`…). Front ends can
  see and assign them, never edit them, and deny policies cannot restrict them.
- **Policies** (ABAC), stored as data and managed from a front end at runtime
  through the `authz_*` resources.

An organisation that already runs OPA, Cedar or OpenFGA can delegate the
policies to it instead: see [external policy engines](authorizers.md).

Business rules ("only drafts can be edited") stay in the domain and the state
machines. Permissions answer whether *this caller* may perform *this action* on
*this record*.

## Setup

```python
from resource_engine.authz import AuthzConfig, IdpRoleSource, PolicyAuthorizer, SystemRole

registry.register_authz()  # authz_policies, authz_roles, authz_role_assignments

authz = AuthzConfig(
    system_roles=[SystemRole.admin(), SystemRole.viewer()],
    role_source=IdpRoleSource({"invoices-admin": "admin", "approver": "approver"}),
    bootstrap={"platform-admin": "admin"},  # cannot lock yourself out
)
manager = ResourceManager(registry, uow, authorizer=PolicyAuthorizer(authz, uow))
mount_resources(app, registry, manager, actor_dependency=bearer_actor(authenticator))
```

Without `authorizer`, authorization is off (every call is allowed). With it, the
default is **deny**: an action needs a system-role grant or an allow policy.
Anonymous callers (`actor=None`) are denied. Engine jobs (consumers, pipelines,
scripts) act as `system_principal("job-name")`, which is always allowed and cannot
come from a token.

With SQLAlchemy: `map_authz(mapper_registry, authz_tables(metadata))`.

## Actions

Every operation has a stable name, listed by `resource-engine inspect`:

| Operation | Action |
|---|---|
| CRUD | `invoices:list`, `invoices:get`, `invoices:create`, `invoices:update`, `invoices:delete` |
| Transition | `invoices:transition:approve` |
| Action | `invoices:action:render_summary` |
| Documents | `invoices:attach:<slot>`, `invoices:detach:<slot>`, `invoices:download:<slot>` |
| Fields | `invoices:read:<field>`, `invoices:write:<field>` (see [Fields](#fields)) |

Patterns: `*` matches one segment and a trailing `*` the rest (`invoices:*`,
`*:list`, `*:download:*`). Resources with `authz_wildcards = False` (the `authz_*`
resources, raw `documents`, anything you mark as sensitive) are only matched by
explicit names, or by system roles with `include_sensitive` (`admin`).

## Where roles come from

A deployment choice:

| Source | Roles |
|---|---|
| `IdpRoleSource(mapping)` | From the token (`Principal.roles` / `groups`), mapped to application names |
| `StoredRoleSource(uow)` | Assigned from the front end (`authz_role_assignments`) to a principal id or a group |
| `CombinedRoleSource(a, b)` | Both |

`bootstrap` maps provider roles to system roles in every case.

System roles with `cross_tenant=True` (`SystemRole.admin()`) act across tenants on
tenant-scoped resources; everyone else is confined to their tenant before any
policy is evaluated (see [tenancy](tenancy.md)).

## Policies

```json
{
  "name": "Approvers decide invoices under 10,000",
  "effect": "allow",
  "subjects": {"roles": ["approver"], "groups": [], "users": [], "everyone": false},
  "actions": ["invoices:transition:approve", "invoices:transition:reject"],
  "condition": {"lt": [{"attr": "resource.amount"}, 10000]},
  "enabled": true
}
```

Evaluation: a system-role grant allows and cannot be denied; otherwise any
applicable deny whose condition holds denies; otherwise an applicable allow whose
condition holds allows; otherwise denied.

### Conditions

JSON objects with one operator: `eq ne lt le gt ge` (two operands), `in nin`
(value, list), `contains` (list, value), `exists`, `and or` (lists), `not`.
Operands are literals or `{"attr": "<namespace>.<path>"}`:

| Namespace | Content |
|---|---|
| `subject` | `id`, `subject`, `roles` (application roles), `groups`, `scopes`, `tenant`, `client_id`, `service`, `email`, `claims.*` |
| `resource` | fields of the read schema, the state field with its states |
| `input` | the validated input of the operation |
| `context` | `action`, `operation`, `now` |

Policies are validated when saved (RE-E703 with `details.problems`: JSON paths
and messages) against the catalogue: unknown actions or attributes, type
mismatches, operators on the wrong types.

## Fields

Every field of the read schema has a `read` action, and every field of the
create/update schemas a `write` action (the id field has none):
`invoices:read:notes`, `invoices:write:amount`.

Fields are **open** by default: whoever can see the record sees the field, and
whoever may update it may change the field. A field becomes **restricted** when:

- the code says so: `restricted_fields = ["notes"]` on the resource; or
- an allow policy names it explicitly (`invoices:read:notes`,
  `invoices:*:notes`). Patterns like `invoices:*` or `invoices:read:*` do not
  restrict anything.

| Field | Allowed when |
|---|---|
| open | no applicable deny holds |
| restricted | a system-role grant reaches it, or an allow policy that names it explicitly holds, and no deny holds |

Like sensitive resources, restricted fields are not reached by broad grants: an
allow on `invoices:*` does not grant `notes`, and the `viewer` role does not see
them. `admin` (`include_sensitive`) sees everything; system roles are never
restricted by denies. Conditions work as for actions and are evaluated per
record:

```json
{
  "name": "Amounts are frozen once submitted",
  "effect": "deny",
  "subjects": {"everyone": true},
  "actions": ["invoices:write:amount"],
  "condition": {"ne": [{"attr": "resource.status"}, "draft"]}
}
```

What callers see:

- **reads** (get, list, and the results of create, update, transitions and
  actions): hidden fields are **omitted**; the key is absent from the JSON, and
  the attribute is missing on the returned model. A rule that depends on data
  not known when reading (`input.*`) hides the field.
- **writes** (create, update): sending a field the caller may not write fails
  with **RE-E706** (403, `details.fields`), and nothing is saved. Only fields
  actually sent count.
- **lists** cannot filter or sort by fields the caller may not read
  (**RE-E707**), so hidden values cannot be inferred.
- `GET /authz/me` returns
  `fields: {resource: {hidden, readonly, conditional}}` to build forms.

Hooks, events and repositories always see full records. OpenAPI keeps the
declared read schemas; clients of APIs with field rules should treat read fields
as optional. Granting a field action follows the no-escalation rule: editors
can grant only fields they hold.

## The lifecycle

| Step | Check | Result when denied |
|---|---|---|
| 2 (before validation) | Can any policy allow it at all? (record unknown) | 403 RE-E702 |
| 5b (record loaded or built) | `get` on the record, then the action with `resource` and `input`; writable fields on create/update | 404 if the record is not visible, else 403 (RE-E702 / RE-E706) |
| 9 (result) | readable fields of the record | hidden fields omitted |
| `list` | Allow conditions on `resource.*` become a row filter (`ListQuery.condition`) | rows outside the scope are not returned; totals are correct |

Transitions listed by `GET /{id}/transitions` are filtered by permission.
Documents inherit their owner's visibility, plus `attach`, `detach` and `download`
per slot.

Row filters need a repository with `supports_conditions` (in-memory, SQLAlchemy:
compiled to SQL). Others fail closed with RE-E705 when a filter is needed.

## Managing permissions from a front end

| Route | Purpose | Needs |
|---|---|---|
| `GET /authz/catalog` | Actions, attributes (with types and enums), operators, system roles | `authz_policies:list` |
| `GET/POST/PATCH/DELETE /authz_policies` | Policies | the matching `authz_policies:*` action |
| `GET/POST/PATCH/DELETE /authz_roles` | Custom roles (system names are reserved) | `authz_roles:*` |
| `GET/POST/DELETE /authz_role_assignments` | Role assignments (with `StoredRoleSource`) | `authz_role_assignments:*` |
| `GET /authz/me` | The caller's roles and actions (`allowed` / `conditional`) to show or hide UI | authenticated |
| `POST /authz/explain` | Why an action is allowed or denied (optionally as another caller, on a record) | `authz_policies:list` |

Rules enforced on changes:

- policies cannot target system roles;
- **no privilege escalation**: an editor can only grant actions they hold
  unconditionally, and only assign roles whose actions they hold;
- changes apply at once on the instance that made them; other instances refresh
  on the `PolicyChanged` event (`authorizer.register(router)` in a consumer) or
  after 60 seconds.

## Documentation

`resource-engine docs generate` writes `permissions.md`, the matrix of system
roles × actions (open field actions are left out; restricted ones are listed), when `[tool.resource-engine] authz = "pkg.bootstrap:authz"` is
set. Policies are data: list them with `GET /authz_policies`.

Refused attempts are stored in the [audit trail](audit.md) when it is enabled.

## External engines

`EngineAuthorizer` puts OPA, Cedar or OpenFGA behind the same port, with the same
actions, system roles, row filters and field rules: see
[external policy engines](authorizers.md).
