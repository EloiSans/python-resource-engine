# authz_policies

`resource_engine.authz.resources.PolicyResource` — entitat `Policy`

Access policies (ABAC), managed from the front end.

A policy allows or denies actions to subjects (roles, groups, users or everyone)
when its condition holds. System roles are fixed in code and cannot be
targeted or restricted here.

## Business area

Permissions

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /authz_policies` |  | PolicyRead | Lists the access policies. |
| `get` | crud | `GET /authz_policies/{id}` |  | PolicyRead | Returns an access policy. |
| `create` | crud | `POST /authz_policies` | PolicyCreate | PolicyRead | Creates an access policy (validated against the catalogue). |
| `update` | crud | `PATCH /authz_policies/{id}` | PolicyUpdate | PolicyRead | Changes an access policy. |
| `delete` | crud | `DELETE /authz_policies/{id}` |  |  | Deletes an access policy. |

Filtres: `effect`, `enabled`, `name` · Ordenació: `name`, `created_at`, `updated_at`

Auditoria: s'auditen els canvis i els intents denegats.

## Esdeveniments de domini

### `PolicyChanged`

`resource_engine.authz.resources.PolicyChanged`

Policies or role assignments changed: caches must be refreshed.

| Camp | Tipus | Obligatori |
|---|---|---|
| `resource` | `str` | sí |
| `entity_id` | `Any` | sí |

## Hooks del cicle de vida

| Punt | Hook | Efectes externs | Descripció |
|---|---|---|---|
| `before_create` | `stamp_created` | no | Records who created the policy and when. |
| `before_create` | `validate_policy` | no | Checks actions, subjects and the condition against the catalogue, and that |
| `before_update` | `stamp_updated` | no | Records who changed the policy last and when. |
| `after_update` | `validate_policy` | no | Checks actions, subjects and the condition against the catalogue, and that |
| `after_create` | `record_change` | no | Emits ``PolicyChanged`` so every instance refreshes its permission caches. |
| `after_delete` | `record_change` | no | Emits ``PolicyChanged`` so every instance refreshes its permission caches. |
| `after_commit` | `refresh_caches` | no | Refreshes this instance's policy and role caches after the commit. |

## Schemas

### PolicyCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Short name |
| `description` | `str \| None` | no | What it allows or denies |
| `effect` | `Literal['allow', 'deny']` | sí | allow or deny |
| `subjects` | `Subjects` | sí | Who it applies to |
| `actions` | `list[str]` | sí | Action patterns, e.g. invoices:* |
| `condition` | `Any` | no | Condition (JSON), see GET /authz/catalog |
| `enabled` | `bool` | no | Disabled policies are ignored |

### PolicyUpdate (update)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str \| None` | no | — |
| `description` | `str \| None` | no | — |
| `effect` | `Literal['allow', 'deny'] \| None` | no | — |
| `subjects` | `Subjects \| None` | no | — |
| `actions` | `list[str] \| None` | no | — |
| `condition` | `Any` | no | — |
| `enabled` | `bool \| None` | no | — |

### PolicyRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `str` | sí | Policy id |
| `name` | `str` | sí | Short name |
| `description` | `str \| None` | no | What it allows or denies |
| `effect` | `str` | sí | allow or deny |
| `subjects` | `dict[str, Any]` | sí | Who it applies to |
| `actions` | `list[str]` | sí | Action patterns |
| `condition` | `Any` | no | Condition (JSON) |
| `enabled` | `bool` | sí | Whether it is evaluated |
| `created_at` | `datetime \| None` | no | Creation time |
| `created_by` | `str \| None` | no | Who created it |
| `updated_at` | `datetime \| None` | no | Last change |
| `updated_by` | `str \| None` | no | Who changed it last |
