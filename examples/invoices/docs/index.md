# Catàleg de recursos

| Recurs | Descripció | Operacions |
|---|---|---|
| [customers](resources/customers.md) | Clients als quals l'organització emet factures. | `list`, `get`, `create`, `update`, `delete` |
| [invoices](resources/invoices.md) | Gestió de factures de l'organització. | `list`, `get`, `create`, `update`, `delete`, `submit`, `approve`, `reject`, `pay`, `reopen`, `recalculate_totals`, `render_summary` |
| [documents](resources/documents.md) | Files attached to entities of other resources. | `list`, `get`, `confirm`, `start_processing`, `reject`, `discard`, `complete` |
| [authz_policies](resources/authz_policies.md) | Access policies (ABAC), managed from the front end. | `list`, `get`, `create`, `update`, `delete` |
| [authz_roles](resources/authz_roles.md) | Custom roles, created from the front end. System roles are defined in code. | `list`, `get`, `create`, `update`, `delete` |
| [authz_role_assignments](resources/authz_role_assignments.md) | Roles given to principals or groups from the front end (used by | `list`, `get`, `create`, `delete` |
| [audit_log](resources/audit_log.md) | Audit trail: committed changes and refused attempts. | `list`, `get` |

Vegeu també la [matriu de permisos](permissions.md).
