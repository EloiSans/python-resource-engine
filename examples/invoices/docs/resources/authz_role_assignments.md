# authz_role_assignments

`resource_engine.authz.resources.RoleAssignmentResource` — entitat `RoleAssignment`

Roles given to principals or groups from the front end (used by

``StoredRoleSource``).

## Business area

Permissions

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /authz_role_assignments` |  | AssignmentRead | Lists role assignments, filterable by role, principal or group. |
| `get` | crud | `GET /authz_role_assignments/{id}` |  | AssignmentRead | Returns a role assignment. |
| `create` | crud | `POST /authz_role_assignments` | AssignmentCreate | AssignmentRead | Gives a role to a principal or a group. |
| `delete` | crud | `DELETE /authz_role_assignments/{id}` |  |  | Removes a role assignment. |

Filtres: `role`, `principal`, `group` · Ordenació: —

Auditoria: s'auditen els canvis i els intents denegats.

## Hooks del cicle de vida

| Punt | Hook | Efectes externs | Descripció |
|---|---|---|---|
| `before_create` | `validate_assignment` | no | Exactly one of principal or group; the role exists; the editor holds every |
| `after_create` | `record_change` | no | Emits ``PolicyChanged`` so every instance refreshes its permission caches. |
| `after_delete` | `record_change` | no | Emits ``PolicyChanged`` so every instance refreshes its permission caches. |
| `after_commit` | `refresh_caches` | no | Refreshes this instance's policy and role caches after the commit. |

## Schemas

### AssignmentCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `role` | `str` | sí | Role name (system or custom) |
| `principal` | `str \| None` | no | Principal id (provider:subject) |
| `group` | `str \| None` | no | Identity provider group |

### AssignmentRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `str` | sí | Assignment id |
| `role` | `str` | sí | Role name |
| `principal` | `str \| None` | no | Principal id |
| `group` | `str \| None` | no | Group |
| `created_at` | `datetime \| None` | no | Creation time |
| `created_by` | `str \| None` | no | Who assigned it |
