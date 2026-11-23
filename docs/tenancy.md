# Tenancy

Multi-tenant back ends must never show one tenant's records to another. Policy
conditions (`subject.tenant`) can express that, but one wrong policy leaks
data. Tenant scoping is a separate guarantee, enforced by the engine whatever
the policies say.

## Declaring a tenant-scoped resource

```python
class TicketResource(Resource):
    model = Ticket
    schemas = {"create": TicketIn, "update": TicketPatch, "read": TicketOut}
    tenant_field = "tenant_id"
```

- The field must be part of the read schema and cannot be the id field.
- The update schema must not contain it (RE-E001): records never move between
  tenants.
- Create schemas usually leave it out: the engine stamps it.

With SQLAlchemy, index the tenant column (or make it the first column of your
composite indexes): every query filters by it.

## Who belongs to which tenant

The caller's tenant is `Principal.tenant`, filled from the token by the identity
presets (Keycloak, Cognito, Entra ID `tid`, Google Identity Platform tenants; see
[identity](identity.md)).

| Caller | Scope |
|---|---|
| Principal with a tenant | confined to it |
| Principal without a tenant, anonymous | refused: **RE-E708** (403) |
| System role with `cross_tenant=True` (`SystemRole.admin()`) | all tenants; `tenant=` narrows to one |
| Engine jobs (`system_principal(...)`) | all tenants; `tenant=` narrows to one |

Without an authorizer there are no roles: only engine jobs act across tenants.

## What the engine does

| Operation | Behaviour |
|---|---|
| `list` | filtered by the tenant (an equality filter, so every repository applies it: SQL `WHERE`, REST query, memory); a conflicting filter returns nothing |
| `get`, `update`, `delete`, transitions, instance actions, documents | a record of another tenant does not exist: **404**, audited as `denied` ("other tenant") |
| `create` | the tenant field is set to the caller's tenant before authorization, so policies see it. Sending another tenant fails with **RE-E709** (403); cross-tenant callers must name one (`tenant=`), otherwise RE-E708 |
| `tenant=` from a confined caller | RE-E709 unless it is their own tenant |

`ctx.tenant` gives hooks and actions the tenant of the operation (`None` for
cross-tenant callers acting on all tenants). Hooks that use `ctx.repository()`
directly are trusted code: filter by `ctx.tenant` there yourself.

Over HTTP, routes of tenant-scoped resources accept `?tenant=`.

## Tenancy, policies and audit

Tenancy is applied first. Policies then decide within the tenant, and can still
use `subject.tenant` and `resource.<tenant_field>`. Audit entries carry the
`tenant`, and `audit_log` can be filtered by it.
