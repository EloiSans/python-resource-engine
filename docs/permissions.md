# Permissions

Who may do what is decided in two layers:

- **System roles**, defined in code or config (`admin`, `viewer`…). Front ends can
  see and assign them, never edit them, and deny policies cannot restrict them.
- **Policies** (ABAC), stored as data and managed from a front end at runtime
  through the `authz_*` resources.

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

## The lifecycle

| Step | Check | Result when denied |
|---|---|---|
| 2 (before validation) | Can any policy allow it at all? (record unknown) | 403 RE-E702 |
| 5b (record loaded or built) | `get` on the record, then the action with `resource` and `input` | 404 if the record is not visible, else 403 |
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
roles × actions, when `[tool.resource-engine] authz = "pkg.bootstrap:authz"` is
set. Policies are data: list them with `GET /authz_policies`.

## Not yet

Field-level permissions (hiding or protecting fields per role), decision audit
log storage, and adapters for external policy engines (Cedar, OPA, OpenFGA)
behind the `Authorizer` port.
