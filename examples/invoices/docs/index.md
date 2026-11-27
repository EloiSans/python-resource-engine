# Catàleg de recursos

| Recurs | Descripció | Operacions | Màquines d'estats |
|---|---|---|---|
| [customers](resources/customers.md) | Clients als quals l'organització emet factures. | `list`, `get`, `create`, `update`, `delete` | — |
| [invoices](resources/invoices.md) | Gestió de factures de l'organització. | `list`, `get`, `create`, `update`, `delete`, `submit`, `approve`, `reject`, `pay`, `reopen`, `send`, `bounce`, `resend`, `recalculate_totals`, `render_summary` | [approval](state-machines.md#invoices-approval), [delivery](state-machines.md#invoices-delivery) |
| [tags](resources/tags.md) | Etiquetes per classificar factures. | `list`, `get`, `create`, `update`, `delete` | — |
| [invoice_tags](resources/invoice_tags.md) | Etiquetes assignades a factures (molts a molts). | `list`, `get`, `create`, `delete` | — |
| [invoice_payments](resources/invoice_payments.md) | Cobrament de les factures aprovades. | `list`, `get`, `create`, `receive`, `expire` | [status](state-machines.md#invoice_payments-status) |
| [customer_balances](resources/customer_balances.md) | Saldo de facturació de cada client, calculat a partir de les factures. | `list`, `get` | — |
| [documents](resources/documents.md) | Files attached to entities of other resources. | `list`, `get`, `confirm`, `start_processing`, `reject`, `discard`, `complete` | [status](state-machines.md#documents-status) |
| [authz_policies](resources/authz_policies.md) | Access policies (ABAC), managed from the front end. | `list`, `get`, `create`, `update`, `delete` | — |
| [authz_roles](resources/authz_roles.md) | Custom roles, created from the front end. System roles are defined in code. | `list`, `get`, `create`, `update`, `delete` | — |
| [authz_role_assignments](resources/authz_role_assignments.md) | Roles given to principals or groups from the front end (used by | `list`, `get`, `create`, `delete` | — |
| [authz_api_keys](resources/authz_api_keys.md) | API keys for machine clients that cannot use OAuth. | `list`, `get`, `create`, `update`, `revoke` | [status](state-machines.md#authz_api_keys-status) |
| [audit_log](resources/audit_log.md) | Audit trail: committed changes and refused attempts. | `list`, `get` | — |

Tots els diagrames d'estats: [màquines d'estats](state-machines.md).

Els correus que envia l'aplicació: [correus](emails.md).

Vegeu també la [matriu de permisos](permissions.md).
