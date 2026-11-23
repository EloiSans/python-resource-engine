# Matriu de permisos (rols de sistema)

Els rols de sistema es defineixen al codi i no es poden modificar des del front-end. Les polítiques creades des del front-end s'han de consultar a `authz_policies`.

| Rol | Descripció | Concessions |
|---|---|---|
| `admin` | Full access, including permission management | `*` |
| `viewer` | Read-only access | `*:list`, `*:get`, `*:download:*` |

| Acció | `admin` | `viewer` |
|---|---|---|
| `invoices:list` | ✓ | ✓ |
| `invoices:get` | ✓ | ✓ |
| `invoices:create` | ✓ |  |
| `invoices:update` | ✓ |  |
| `invoices:delete` | ✓ |  |
| `invoices:transition:submit` | ✓ |  |
| `invoices:transition:approve` | ✓ |  |
| `invoices:transition:reject` | ✓ |  |
| `invoices:transition:pay` | ✓ |  |
| `invoices:transition:reopen` | ✓ |  |
| `invoices:action:recalculate_totals` | ✓ |  |
| `invoices:action:render_summary` | ✓ |  |
| `invoices:attach:signed_pdf` | ✓ |  |
| `invoices:detach:signed_pdf` | ✓ |  |
| `invoices:download:signed_pdf` | ✓ | ✓ |
| `invoices:attach:attachments` | ✓ |  |
| `invoices:detach:attachments` | ✓ |  |
| `invoices:download:attachments` | ✓ | ✓ |
| `invoices:attach:summary` | ✓ |  |
| `invoices:detach:summary` | ✓ |  |
| `invoices:download:summary` | ✓ | ✓ |
| `invoices:read:notes` | ✓ |  |
| `invoices:write:notes` | ✓ |  |
| `documents:list` | ✓ |  |
| `documents:get` | ✓ |  |
| `documents:transition:confirm` | ✓ |  |
| `documents:transition:start_processing` | ✓ |  |
| `documents:transition:reject` | ✓ |  |
| `documents:transition:discard` | ✓ |  |
| `documents:transition:complete` | ✓ |  |
| `authz_policies:list` | ✓ |  |
| `authz_policies:get` | ✓ |  |
| `authz_policies:create` | ✓ |  |
| `authz_policies:update` | ✓ |  |
| `authz_policies:delete` | ✓ |  |
| `authz_roles:list` | ✓ |  |
| `authz_roles:get` | ✓ |  |
| `authz_roles:create` | ✓ |  |
| `authz_roles:update` | ✓ |  |
| `authz_roles:delete` | ✓ |  |
| `authz_role_assignments:list` | ✓ |  |
| `authz_role_assignments:get` | ✓ |  |
| `authz_role_assignments:create` | ✓ |  |
| `authz_role_assignments:delete` | ✓ |  |
