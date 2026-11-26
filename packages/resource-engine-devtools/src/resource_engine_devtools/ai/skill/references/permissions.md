# Permissions

Two layers decide who may do what:

- **System roles** in code (`AuthzConfig(system_roles=[SystemRole.admin(),
  SystemRole.viewer()])`). They grant action patterns and cannot be denied.
- **Policies** (ABAC) stored as data in `authz_policies` and managed from a front
  end at runtime. Or an external engine (OPA, Cedar, OpenFGA) behind
  `EngineAuthorizer`.

Business rules ("only drafts can be edited") stay in the domain. Permissions
answer whether *this caller* may perform *this action* on *this record*.
**Never check roles or users in hooks.**

With an authorizer configured, the default is **deny**. Without one, everything
is allowed. Engine jobs act as `system_principal("job-name")`, always allowed.

## Action names

| Operation | Action |
|---|---|
| CRUD | `invoices:list`, `invoices:get`, `invoices:create`, `invoices:update`, `invoices:delete` |
| Transition | `invoices:transition:approve` |
| Action | `invoices:action:recalculate_totals` |
| Documents | `invoices:attach:<slot>`, `invoices:detach:<slot>`, `invoices:download:<slot>` |
| Fields | `invoices:read:<field>`, `invoices:write:<field>` |

`resource-engine inspect --json` lists them. Patterns: `*` matches one segment,
a trailing `*` the rest (`invoices:*`, `*:list`).

## Policies (data)

```json
{
  "name": "Approvers decide invoices under 10,000",
  "effect": "allow",
  "subjects": {"roles": ["approver"]},
  "actions": ["invoices:transition:approve", "invoices:transition:reject"],
  "condition": {"lt": [{"attr": "resource.amount"}, 10000]}
}
```

Conditions: one operator per object, `eq ne lt le gt ge`, `in nin`, `contains`,
`exists`, `and or not`. Operands are literals or `{"attr": "<ns>.<path>"}` with
namespaces `subject` (`id`, `roles`, `groups`, `tenant`, `claims.*`…),
`resource` (read-schema fields), `input` (validated input), `context`
(`action`, `operation`, `now`).

Row-level conditions on `list` become SQL filters. A deny that holds wins over
any allow; system-role grants win over everything. Policies are validated when
saved (RE-E703).

## Fields

Fields are open by default. Mark sensitive ones on the resource:

```python
restricted_fields = ["notes"]  # needs invoices:read:notes / invoices:write:notes
```

Hidden fields are omitted from responses. Writing one fails with RE-E706,
filtering or sorting by one with RE-E707.

## Tenants

```python
tenant_field = "org"  # entity attribute holding the tenant
```

Every operation is confined to `Principal.tenant` before any policy is
evaluated. The engine stamps the tenant on create. Don't add tenant checks in
hooks or tenant filters in queries.

## Every operation needs a gate

When you add an operation, transition, action or document slot, also add who
may use it: a policy in the application's seed (the list named by
`[tool.resource-engine] policies`), or a system-role grant. `resource-engine
check` reports operations that only `admin` can perform (RE-C004).

## In tests

```python
manager = ResourceManager(
    registry, store.unit_of_work, authorizer=PolicyAuthorizer(config, store.unit_of_work)
)
user = Principal(subject="ana", issuer="idp", roles=frozenset({"approver"}))
with pytest.raises(PermissionDenied):
    await manager.transition("invoices", 1, "approve", actor=user)
```

## Machine clients

Clients that cannot use OAuth client credentials get API keys
(`registry.register_api_keys()`, `ApiKeyAuthenticator(uow_factory,
fallback=oidc)` in `bearer_actor`), not a hand-made token table or a shared
secret in settings. Keys carry roles (at most the creator's), a tenant and an
expiry, and are revoked with the `revoke` transition. See docs/api-keys.md.

## Rate limits

Limits are configuration, not code in hooks or endpoints: a `RateLimiter`
(`default` per caller, `anonymous` per address, `failed_auth`, `actions` per
engine action) passed to `ResourceManager(rate_limiter=)` and
`bearer_actor(..., rate_limiter=)`; `RedisRateLimitStore` with several
instances. Refusals are RE-E710 (429). See docs/rate-limits.md.
