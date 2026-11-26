# audit_log

`resource_engine.audit.resource.AuditLogResource` — entitat `AuditEntry`

Audit trail: committed changes and refused attempts.

Entries are written by the engine only (no create, update or delete). The
resource is sensitive: wildcard grants do not reach it.

## Business area

Audit

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /audit_log` |  | AuditEntryRead | Lists audit entries; filter by resource and entity_id for a record's history. |
| `get` | crud | `GET /audit_log/{id}` |  | AuditEntryRead | Returns an audit entry. |

Filtres: `resource`, `entity_id`, `tenant`, `actor`, `outcome`, `operation`, `correlation_id`, `seal` · Ordenació: `at`

## Schemas

### AuditEntryRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `str` | sí | Entry id |
| `at` | `datetime` | sí | When it happened (UTC) |
| `actor` | `str \| None` | no | Principal id, system:<job> or null (anonymous) |
| `actor_roles` | `list[str]` | sí | Application roles of the actor at the time |
| `resource` | `str` | sí | Resource name |
| `entity_id` | `str \| None` | no | Target record |
| `tenant` | `str \| None` | no | Tenant of the record or operation |
| `operation` | `str` | sí | create, update, delete, transition, action, attach... |
| `action` | `str` | sí | Permission action, e.g. transition:approve |
| `outcome` | `str` | sí | ok, denied or failed |
| `changes` | `dict[str, Any]` | sí | field → {from, to}; masked fields → {changed: true} |
| `details` | `dict[str, Any]` | sí | Transition, input, documents, denial reason |
| `correlation_id` | `str \| None` | no | Shared with logs and events |
| `seal` | `int \| None` | no | Seal covering the entry; null until sealed |
