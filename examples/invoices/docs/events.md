# Esdeveniments publicats

Missatges que l'aplicació publica després de cada canvi confirmat.

## customers

Clients als quals l'organització emet factures.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | customers: a record was created. | `resource` = `customers`, `entity_id` |
| `ResourceUpdated` | customers: fields of a record changed. | `resource` = `customers`, `entity_id`, `changes` |
| `ResourceDeleted` | customers: a record was deleted. | `resource` = `customers`, `entity_id` |

## invoices

Gestió de factures de l'organització.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoices: a record was created. | `resource` = `invoices`, `entity_id` |
| `ResourceUpdated` | invoices: fields of a record changed. | `resource` = `invoices`, `entity_id`, `changes` |
| `ResourceDeleted` | invoices: a record was deleted. | `resource` = `invoices`, `entity_id` |
| `TransitionExecuted` | invoices: a record changed state. | `resource` = `invoices`, `entity_id`, `transition` (submit, approve, reject, pay, reopen, send, bounce, resend), `source` (draft, pending, approved, rejected, paid, unsent, sent, returned), `target` (draft, pending, approved, rejected, paid, unsent, sent, returned), `machine` (approval, delivery) |
| `ActionExecuted` | invoices: an action ran on a record. | `resource` = `invoices`, `entity_id`, `action` (recalculate_totals, render_summary) |
| `DocumentAttached` | invoices: a document was attached to a record. | `resource` = `invoices`, `entity_id`, `document_id`, `slot` (signed_pdf, attachments, summary), `content_type`, `size`, `processing` |
| `DocumentDetached` | invoices: a document of a record was removed. | `resource` = `invoices`, `entity_id`, `document_id`, `slot` (signed_pdf, attachments, summary) |
| `InvoiceTotalsRecalculated` | L'import d'una factura s'ha recalculat aplicant un descompte. | `invoice_id`, `previous_amount`, `new_amount` |

## tags

Etiquetes per classificar factures.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | tags: a record was created. | `resource` = `tags`, `entity_id` |
| `ResourceUpdated` | tags: fields of a record changed. | `resource` = `tags`, `entity_id`, `changes` |
| `ResourceDeleted` | tags: a record was deleted. | `resource` = `tags`, `entity_id` |

## invoice_tags

Etiquetes assignades a factures (molts a molts).

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoice_tags: a record was created. | `resource` = `invoice_tags`, `entity_id` |
| `ResourceDeleted` | invoice_tags: a record was deleted. | `resource` = `invoice_tags`, `entity_id` |

## invoice_payments

Cobrament de les factures aprovades.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | invoice_payments: a record was created. | `resource` = `invoice_payments`, `entity_id` |
| `TransitionExecuted` | invoice_payments: a record changed state. | `resource` = `invoice_payments`, `entity_id`, `transition` (receive, expire), `source` (awaiting, overdue, settled), `target` (awaiting, overdue, settled), `machine` (status) |
| `PaymentOverdue` | Una factura aprovada fa 15 dies que espera el cobrament. | `invoice_id` |

## authz_policies

Access policies (ABAC), managed from the front end.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_policies: a record was created. | `resource` = `authz_policies`, `entity_id` |
| `ResourceUpdated` | authz_policies: fields of a record changed. | `resource` = `authz_policies`, `entity_id`, `changes` |
| `ResourceDeleted` | authz_policies: a record was deleted. | `resource` = `authz_policies`, `entity_id` |
| `PolicyChanged` | Policies or role assignments changed: caches must be refreshed. | `resource`, `entity_id` |

## authz_roles

Custom roles, created from the front end. System roles are defined in code.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_roles: a record was created. | `resource` = `authz_roles`, `entity_id` |
| `ResourceUpdated` | authz_roles: fields of a record changed. | `resource` = `authz_roles`, `entity_id`, `changes` |
| `ResourceDeleted` | authz_roles: a record was deleted. | `resource` = `authz_roles`, `entity_id` |

## authz_role_assignments

Roles given to principals or groups from the front end (used by ``StoredRoleSource``).

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_role_assignments: a record was created. | `resource` = `authz_role_assignments`, `entity_id` |
| `ResourceDeleted` | authz_role_assignments: a record was deleted. | `resource` = `authz_role_assignments`, `entity_id` |
| `PolicyChanged` | Policies or role assignments changed: caches must be refreshed. | `resource`, `entity_id` |

## authz_api_keys

API keys for machine clients that cannot use OAuth.

| Esdeveniment | Descripció | Dades |
|---|---|---|
| `ResourceCreated` | authz_api_keys: a record was created. | `resource` = `authz_api_keys`, `entity_id` |
| `ResourceUpdated` | authz_api_keys: fields of a record changed. | `resource` = `authz_api_keys`, `entity_id`, `changes` |
| `TransitionExecuted` | authz_api_keys: a record changed state. | `resource` = `authz_api_keys`, `entity_id`, `transition` (revoke), `source` (active, revoked), `target` (active, revoked), `machine` (status) |
