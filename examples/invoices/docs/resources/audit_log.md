# audit_log

Audit trail: committed changes and refused attempts.

Entries are written by the engine only (no create, update or delete). The
resource is sensitive: wildcard grants do not reach it.

## Àmbit de negoci

Audit

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Lists audit entries; filter by resource and entity_id for a record's history. |
| `get` | crud | Returns an audit entry. |
