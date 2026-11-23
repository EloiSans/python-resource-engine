# authz_roles

`resource_engine.authz.resources.RoleResource` — entitat `Role`

Custom roles, created from the front end. System roles are defined in code.

## Business area

Permissions

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /authz_roles` |  | RoleRead | Lists the custom roles. |
| `get` | crud | `GET /authz_roles/{id}` |  | RoleRead | Returns a custom role. |
| `create` | crud | `POST /authz_roles` | RoleCreate | RoleRead | Creates a custom role. |
| `update` | crud | `PATCH /authz_roles/{id}` | RoleUpdate | RoleRead | Changes a custom role's description. |
| `delete` | crud | `DELETE /authz_roles/{id}` |  |  | Deletes a custom role. |

Filtres: — · Ordenació: `name`

## Hooks del cicle de vida

| Punt | Hook | Efectes externs | Descripció |
|---|---|---|---|
| `before_create` | `validate_role` | no | Custom roles cannot reuse system role names. |

## Schemas

### RoleCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Role name |
| `description` | `str \| None` | no | What the role is for |

### RoleUpdate (update)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `description` | `str \| None` | no | — |

### RoleRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Role name |
| `description` | `str \| None` | no | What the role is for |
| `created_at` | `datetime.datetime \| None` | no | Creation time |
