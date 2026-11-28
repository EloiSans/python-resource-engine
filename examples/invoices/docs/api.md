# API HTTP

Invoices (Resource Engine example) 0.1.0, a partir del document OpenAPI de l'aplicació: [`openapi.json`](openapi.json).

## audit_log

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/audit_log` | Lists audit entries; filter by resource and entity_id for a record's history. | — | 200 `AuditEntryReadPage`, 422 `HTTPValidationError` |
| GET | `/audit_log/export` | Export audit_log as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/audit_log/exports` | Export audit_log in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/audit_log/{entity_id}` | Returns an audit entry. | — | 200 `AuditEntryRead`, 422 `HTTPValidationError` |

## authz

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/authz/catalog` | Actions, attributes and operators for policy editors | — | 200 `object` |
| GET | `/authz/me` | The caller's roles and permitted actions | — | 200 `object` |
| POST | `/authz/explain` | Why an action is allowed or denied | `ExplainRequest` | 200 `object`, 422 `HTTPValidationError` |

## authz_api_keys

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/authz_api_keys` | Lists the API keys (never their secrets). | — | 200 `ApiKeyReadPage`, 422 `HTTPValidationError` |
| POST | `/authz_api_keys` | Creates an API key; the response carries the full key, once. | `ApiKeyCreate` | 201 `ApiKeyRead`, 422 `HTTPValidationError` |
| GET | `/authz_api_keys/export` | Export authz_api_keys as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/authz_api_keys/exports` | Export authz_api_keys in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/authz_api_keys/{entity_id}` | Returns an API key (never its secret). | — | 200 `ApiKeyRead`, 422 `HTTPValidationError` |
| PATCH | `/authz_api_keys/{entity_id}` | Renames a key or changes its expiry. | `ApiKeyUpdate` | 200 `ApiKeyRead`, 422 `HTTPValidationError` |
| GET | `/authz_api_keys/{entity_id}/transitions` | Transitions allowed from the current state | — | 200 list[`string`], 422 `HTTPValidationError` |
| GET | `/authz_api_keys/{entity_id}/history` | State history | — | 200 list[`StateChange`], 422 `HTTPValidationError` |
| POST | `/authz_api_keys/{entity_id}/transitions/revoke` | Transition: revoke | — | 200 `ApiKeyRead`, 422 `HTTPValidationError` |

## authz_policies

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/authz_policies` | Lists the access policies. | — | 200 `PolicyReadPage`, 422 `HTTPValidationError` |
| POST | `/authz_policies` | Creates an access policy (validated against the catalogue). | `PolicyCreate` | 201 `PolicyRead`, 422 `HTTPValidationError` |
| GET | `/authz_policies/export` | Export authz_policies as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/authz_policies/exports` | Export authz_policies in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/authz_policies/{entity_id}` | Returns an access policy. | — | 200 `PolicyRead`, 422 `HTTPValidationError` |
| PATCH | `/authz_policies/{entity_id}` | Changes an access policy. | `PolicyUpdate` | 200 `PolicyRead`, 422 `HTTPValidationError` |
| DELETE | `/authz_policies/{entity_id}` | Deletes an access policy. | — | 204, 422 `HTTPValidationError` |

## authz_role_assignments

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/authz_role_assignments` | Lists role assignments, filterable by role, principal or group. | — | 200 `AssignmentReadPage`, 422 `HTTPValidationError` |
| POST | `/authz_role_assignments` | Gives a role to a principal or a group. | `AssignmentCreate` | 201 `AssignmentRead`, 422 `HTTPValidationError` |
| GET | `/authz_role_assignments/export` | Export authz_role_assignments as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/authz_role_assignments/exports` | Export authz_role_assignments in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/authz_role_assignments/{entity_id}` | Returns a role assignment. | — | 200 `AssignmentRead`, 422 `HTTPValidationError` |
| DELETE | `/authz_role_assignments/{entity_id}` | Removes a role assignment. | — | 204, 422 `HTTPValidationError` |

## authz_roles

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/authz_roles` | Lists the custom roles. | — | 200 `RoleReadPage`, 422 `HTTPValidationError` |
| POST | `/authz_roles` | Creates a custom role. | `RoleCreate` | 201 `RoleRead`, 422 `HTTPValidationError` |
| GET | `/authz_roles/export` | Export authz_roles as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/authz_roles/exports` | Export authz_roles in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/authz_roles/{entity_id}` | Returns a custom role. | — | 200 `RoleRead`, 422 `HTTPValidationError` |
| PATCH | `/authz_roles/{entity_id}` | Changes a custom role's description. | `RoleUpdate` | 200 `RoleRead`, 422 `HTTPValidationError` |
| DELETE | `/authz_roles/{entity_id}` | Deletes a custom role. | — | 204, 422 `HTTPValidationError` |

## customer_balances

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/customer_balances` | Llista els saldos dels clients amb factures, ordenables per import. | — | 200 `CustomerBalanceReadPage`, 422 `HTTPValidationError` |
| GET | `/customer_balances/export` | Export customer_balances as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/customer_balances/exports` | Export customer_balances in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/customer_balances/{entity_id}` | Consulta el saldo d'un client. | — | 200 `CustomerBalanceRead`, 422 `HTTPValidationError` |

## customers

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/customers` | Llista els clients, filtrables per país i nom. | — | 200 `CustomerReadPage`, 422 `HTTPValidationError` |
| POST | `/customers` | Dona d'alta un client. | `CustomerCreate` | 201 `CustomerRead`, 422 `HTTPValidationError` |
| GET | `/customers/export` | Export customers as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/customers/exports` | Export customers in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/customers/{entity_id}` | Consulta un client. | — | 200 `CustomerRead`, 422 `HTTPValidationError` |
| PATCH | `/customers/{entity_id}` | Modifica les dades d'un client. | `CustomerUpdate` | 200 `CustomerRead`, 422 `HTTPValidationError` |
| DELETE | `/customers/{entity_id}` | Elimina un client que no té factures. | — | 204, 422 `HTTPValidationError` |
| GET | `/customers/{entity_id}/invoices` | invoices of one customers record | — | 200 `InvoiceReadPage`, 422 `HTTPValidationError` |
| GET | `/customers/{entity_id}/balance` | customer_balances of one customers record | — | 200 `CustomerBalanceReadPage`, 422 `HTTPValidationError` |

## documents

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/documents/{document_id}/content` | Download a document | — | 200, 307, 422 `HTTPValidationError` |
| POST | `/documents/{document_id}/confirm` | Confirm a presigned upload | — | 200 `DocumentRead`, 422 `HTTPValidationError` |
| GET | `/documents` | Lists documents, filterable by owner, slot and status. | — | 200 `DocumentReadPage`, 422 `HTTPValidationError` |
| GET | `/documents/export` | Export documents as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/documents/exports` | Export documents in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/documents/{entity_id}` | Returns a document's metadata. | — | 200 `DocumentRead`, 422 `HTTPValidationError` |

## exports

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/exports/{export_id}` | Status of a background export | — | 200 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/exports/{export_id}/content` | Download a finished export | — | 200, 307, 422 `HTTPValidationError` |

## invoice_payments

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/invoice_payments` | Llista els cobraments en curs i acabats. | — | 200 `PaymentReadPage`, 422 `HTTPValidationError` |
| GET | `/invoice_payments/export` | Export invoice_payments as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/invoice_payments/exports` | Export invoice_payments in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/invoice_payments/{entity_id}` | Consulta el cobrament d'una factura. | — | 200 `PaymentRead`, 422 `HTTPValidationError` |

## invoice_tags

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/invoice_tags` | Llista les assignacions d'etiquetes. | — | 200 `InvoiceTagReadPage`, 422 `HTTPValidationError` |
| POST | `/invoice_tags` | Assigna una etiqueta a una factura. | `InvoiceTagCreate` | 201 `InvoiceTagRead`, 422 `HTTPValidationError` |
| GET | `/invoice_tags/export` | Export invoice_tags as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/invoice_tags/exports` | Export invoice_tags in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/invoice_tags/{entity_id}` | Consulta una assignació. | — | 200 `InvoiceTagRead`, 422 `HTTPValidationError` |
| DELETE | `/invoice_tags/{entity_id}` | Treu una etiqueta d'una factura. | — | 204, 422 `HTTPValidationError` |

## invoices

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/invoices` | Llista les factures, filtrables per estat i client. | — | 200 `InvoiceReadPage`, 422 `HTTPValidationError` |
| POST | `/invoices` | Crea una factura en estat esborrany. | `InvoiceCreate` | 201 `InvoiceRead`, 422 `HTTPValidationError` |
| GET | `/invoices/export` | Export invoices as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/invoices/exports` | Export invoices in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}` | Consulta una factura. | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| PATCH | `/invoices/{entity_id}` | Modifica una factura mentre és un esborrany. | `InvoiceUpdate` | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| DELETE | `/invoices/{entity_id}` | Elimina una factura en esborrany. | — | 204, 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}/transitions` | Transitions allowed from the current state | — | 200 list[`string`], 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}/history` | State history | — | 200 list[`StateChange`], 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/submit` | Transition: submit | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/approve` | Transition: approve | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/reject` | Transition: reject | `RejectInvoice` | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/pay` | Transition: pay | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/reopen` | Transition: reopen | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/send` | Transition: send | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/bounce` | Transition: bounce | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/transitions/resend` | Transition: resend | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/actions/recalculate_totals` | Aplica un descompte percentual a l'import d'una factura. | `ApplyDiscount` | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/actions/render_summary` | Genera un resum en Markdown de la factura i l'adjunta a la ranura ``summary``. | — | 200 `InvoiceRead`, 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}/payments` | invoice_payments of one invoices record | — | 200 `PaymentReadPage`, 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}/tags` | tags of one invoices record | — | 200 `TagReadPage`, 422 `HTTPValidationError` |
| GET | `/invoices/{entity_id}/documents` | Documents of a invoices entity | — | 200 list[`DocumentRead`], 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/documents/{slot}` | Upload a document to a invoices entity | `Body_invoices_attach_document` (multipart/form-data) | 201 `DocumentRead`, 422 `HTTPValidationError` |
| POST | `/invoices/{entity_id}/documents/{slot}/uploads` | Request a direct (presigned) upload | `UploadRequest` | 201 `UploadTicketOut`, 422 `HTTPValidationError` |
| DELETE | `/invoices/{entity_id}/documents/{document_id}` | Detach a document | — | 204, 422 `HTTPValidationError` |

## tags

| Mètode | Ruta | Resum | Cos | Respostes |
|---|---|---|---|---|
| GET | `/tags` | Llista les etiquetes. | — | 200 `TagReadPage`, 422 `HTTPValidationError` |
| POST | `/tags` | Crea una etiqueta. | `TagCreate` | 201 `TagRead`, 422 `HTTPValidationError` |
| GET | `/tags/export` | Export tags as CSV or XLSX | — | 200, 422 `HTTPValidationError` |
| POST | `/tags/exports` | Export tags in the background | — | 202 `ExportJobOut`, 422 `HTTPValidationError` |
| GET | `/tags/{entity_id}` | Consulta una etiqueta. | — | 200 `TagRead`, 422 `HTTPValidationError` |
| PATCH | `/tags/{entity_id}` | Canvia el nom o el color d'una etiqueta. | `TagCreate` | 200 `TagRead`, 422 `HTTPValidationError` |
| DELETE | `/tags/{entity_id}` | Elimina una etiqueta i les seves assignacions. | — | 204, 422 `HTTPValidationError` |
| GET | `/tags/{entity_id}/invoices` | invoices of one tags record | — | 200 `InvoiceReadPage`, 422 `HTTPValidationError` |

## Esquemes

### ApiKeyCreate

A new key for a machine client.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | What the key is for |
| `roles` | list[`string`] | sí | Roles the key acts with (at most the creator's) |
| `expires_at` | `string` \| `null` | no | When it stops working |
| `tenant` | `string` \| `null` | no | Tenant it acts in (default: the creator's) |

### ApiKeyRead

An API key, without its secret.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Key id: the key starts with rek_<id>_ |
| `name` | `string` | sí | Name |
| `roles` | list[`string`] | sí | Roles |
| `tenant` | `string` \| `null` | no | Tenant |
| `status` | `string` | sí | active or revoked |
| `expires_at` | `string` \| `null` | no | Expires At |
| `last_used_at` | `string` \| `null` | no | Last request (to a minute) |
| `created_at` | `string` \| `null` | no | Created At |
| `created_by` | `string` \| `null` | no | Created By |
| `key` | `string` \| `null` | no | The full key, only in the create response: store it now, it is not shown again |

### ApiKeyReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`ApiKeyRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### ApiKeyUpdate

Rename a key or move its expiry (roles never change: create another key).

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` \| `null` | no | Name |
| `expires_at` | `string` \| `null` | no | Expires At |

### ApplyDiscount

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `percent` | `number` \| `string` | sí | Percent |

### AssignmentCreate

Give a role to a principal or a group (exactly one of them).

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `role` | `string` | sí | Role name (system or custom) |
| `principal` | `string` \| `null` | no | Principal id (provider:subject) |
| `group` | `string` \| `null` | no | Identity provider group |

### AssignmentRead

A role assignment.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Assignment id |
| `role` | `string` | sí | Role name |
| `principal` | `string` \| `null` | no | Principal id |
| `group` | `string` \| `null` | no | Group |
| `created_at` | `string` \| `null` | no | Creation time |
| `created_by` | `string` \| `null` | no | Who assigned it |

### AssignmentReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`AssignmentRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### AuditEntryRead

An audit entry.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Entry id |
| `at` | `string` | sí | When it happened (UTC) |
| `actor` | `string` \| `null` | no | Principal id, system:<job> or null (anonymous) |
| `actor_roles` | list[`string`] | sí | Application roles of the actor at the time |
| `resource` | `string` | sí | Resource name |
| `entity_id` | `string` \| `null` | no | Target record |
| `tenant` | `string` \| `null` | no | Tenant of the record or operation |
| `operation` | `string` | sí | create, update, delete, transition, action, attach... |
| `action` | `string` | sí | Permission action, e.g. transition:approve |
| `outcome` | `string` | sí | ok, denied or failed |
| `changes` | `object` | sí | field → {from, to}; masked fields → {changed: true} |
| `details` | `object` | sí | Transition, input, documents, denial reason |
| `correlation_id` | `string` \| `null` | no | Shared with logs and events |
| `seal` | `integer` \| `null` | no | Seal covering the entry; null until sealed |

### AuditEntryReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`AuditEntryRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### Body_invoices_attach_document

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `file` | `string` | sí | File |

### CustomerBalanceRead

Saldo d'un client: les seves factures sumades per estat.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer_id` | `integer` | sí | Client (customers) |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |
| `invoices` | `integer` | sí | Nombre de factures |
| `total` | `string` | sí | Import total de les factures |
| `outstanding` | `string` | sí | Import pendent d'aprovar o de cobrar |
| `paid` | `string` | sí | Import cobrat |
| `last_issue_date` | `string` \| `null` | sí | Data d'emissió més recent |

### CustomerBalanceReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`CustomerBalanceRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### CustomerCreate

Dades per donar d'alta un client.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Raó social |
| `tax_id` | `string` | sí | NIF |
| `country` | `string` | no | País (ISO 3166) |
| `email` | `string` \| `null` | no | Adreça de facturació |

### CustomerRead

Representació pública d'un client.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `integer` | sí | Id |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |
| `name` | `string` | sí | Name |
| `tax_id` | `string` | sí | Tax Id |
| `country` | `string` | sí | Country |
| `email` | `string` \| `null` | sí | Email |

### CustomerReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`CustomerRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### CustomerUpdate

Camps modificables d'un client.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` \| `null` | no | Name |
| `country` | `string` \| `null` | no | Country |
| `email` | `string` \| `null` | no | Email |

### DocumentRead

Public representation of a document.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Document identifier |
| `owner_resource` | `string` | sí | Resource the document belongs to |
| `owner_id` | `string` | sí | Identifier of the owning entity |
| `slot` | `string` | sí | Slot of the owning resource |
| `status` | `string` | sí | pending, processing, available, rejected or deleted |
| `filename` | `string` | sí | Original file name (sanitised) |
| `content_type` | `string` | sí | MIME type |
| `size` | `integer` \| `null` | no | Size in bytes |
| `sha256` | `string` \| `null` | no | SHA-256 of the content, hex |
| `created_at` | `string` \| `null` | no | When it was attached |
| `created_by` | `string` \| `null` | no | Actor that attached it |
| `metadata` | `object` | no | Data from processing |
| `error` | `string` \| `null` | no | Why processing rejected it |

### DocumentReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`DocumentRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### ExplainAs

A hypothetical caller, described as its identity provider would.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `subject` | `string` | no | Subject |
| `roles` | list[`string`] | no | Provider roles |
| `groups` | list[`string`] | no | Groups |
| `tenant` | `string` \| `null` | no | Tenant |

### ExplainRequest

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `action` | `string` | sí | Full action name, e.g. invoices:transition:approve |
| `entity_id` | `object` | no | Record to evaluate against |
| `as` | `ExplainAs` \| `null` | no | Evaluate for this caller instead of yourself |

### ExportError

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `code` | `string` | sí | Code |
| `message` | `string` | sí | Message |

### ExportJobOut

A background export.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Export id |
| `resource` | `string` | sí | Resource |
| `format` | `string` | sí | Format |
| `status` | `string` | sí | pending, running, done (download it) or failed (see error) |
| `rows` | `integer` \| `null` | no | Rows that matched when requested |
| `filename` | `string` \| `null` | no | Filename |
| `size` | `integer` \| `null` | no | Size of the file in bytes |
| `created_at` | `string` | sí | Created At |
| `started_at` | `string` \| `null` | no | Started At |
| `finished_at` | `string` \| `null` | no | Finished At |
| `expires_at` | `string` \| `null` | no | When the file is deleted |
| `error` | `ExportError` \| `null` | no |  |

### HTTPValidationError

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `detail` | list[`ValidationError`] | no | Detail |

### InvoiceCreate

Dades per crear una factura.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer` | `string` | sí | Nom del client |
| `amount` | `number` \| `string` | sí | Import total |
| `issue_date` | `string` \| `null` | no | Data d'emissió |
| `customer_id` | `integer` \| `null` | no | Client registrat (customers); el nom de `customer` es manté per compatibilitat |
| `notes` | `string` \| `null` | no | Notes |

### InvoiceRead

Representació pública d'una factura.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `integer` | sí | Id |
| `customer` | `string` | sí | Customer |
| `amount` | `string` | sí | Amount |
| `issue_date` | `string` \| `null` | sí | Issue Date |
| `customer_id` | `integer` \| `null` | no | Client registrat (customers); el nom de `customer` es manté per compatibilitat |
| `notes` | `string` \| `null` | sí | Notes |
| `status` | `string` | sí | Status |
| `rejection_reason` | `string` \| `null` | sí | Rejection Reason |
| `status_changed_at` | `string` \| `null` | no | Quan va entrar a l'estat actual (el posa el motor) |
| `delivery_status` | `string` | no | Estat de l'enviament al client (màquina delivery) |
| `version` | `integer` | sí | Versió del registre (ETag); envia-la a If-Match |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |

### InvoiceReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`InvoiceRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### InvoiceTagCreate

Etiqueta assignada a una factura.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `invoice_id` | `integer` | sí | Factura |
| `tag_id` | `integer` | sí | Etiqueta |

### InvoiceTagRead

Assignació d'una etiqueta a una factura.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `invoice_id` | `integer` | sí | Factura |
| `tag_id` | `integer` | sí | Etiqueta |
| `id` | `integer` | sí | Id |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |

### InvoiceTagReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`InvoiceTagRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### InvoiceUpdate

Camps modificables d'una factura en esborrany.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer` | `string` \| `null` | no | Customer |
| `amount` | `number` \| `string` \| `null` | no | Amount |
| `issue_date` | `string` \| `null` | no | Issue Date |
| `customer_id` | `integer` \| `null` | no | Customer Id |
| `notes` | `string` \| `null` | no | Notes |

### PaymentRead

Estat del cobrament d'una factura.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `invoice_id` | `integer` | sí | Factura aprovada |
| `amount` | `string` | sí | Import a cobrar |
| `id` | `integer` | sí | Id |
| `status` | `string` | sí | awaiting, overdue o settled |
| `status_changed_at` | `string` \| `null` | no | Últim canvi d'estat |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |

### PaymentReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`PaymentRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### PolicyCreate

A new access policy.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Short name |
| `description` | `string` \| `null` | no | What it allows or denies |
| `effect` | `string` | sí | allow or deny |
| `subjects` | `Subjects` | sí | Who it applies to |
| `actions` | list[`string`] | sí | Action patterns, e.g. invoices:* |
| `condition` | `object` | no | Condition (JSON), see GET /authz/catalog |
| `enabled` | `boolean` | no | Disabled policies are ignored |

### PolicyRead

An access policy.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `string` | sí | Policy id |
| `name` | `string` | sí | Short name |
| `description` | `string` \| `null` | no | What it allows or denies |
| `effect` | `string` | sí | allow or deny |
| `subjects` | `object` | sí | Who it applies to |
| `actions` | list[`string`] | sí | Action patterns |
| `condition` | `object` | no | Condition (JSON) |
| `enabled` | `boolean` | sí | Whether it is evaluated |
| `created_at` | `string` \| `null` | no | Creation time |
| `created_by` | `string` \| `null` | no | Who created it |
| `updated_at` | `string` \| `null` | no | Last change |
| `updated_by` | `string` \| `null` | no | Who changed it last |

### PolicyReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`PolicyRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### PolicyUpdate

Changes to a policy.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` \| `null` | no | Name |
| `description` | `string` \| `null` | no | Description |
| `effect` | `string` \| `null` | no | Effect |
| `subjects` | `Subjects` \| `null` | no |  |
| `actions` | list[`string`] \| `null` | no | Actions |
| `condition` | `object` | no | Condition |
| `enabled` | `boolean` \| `null` | no | Enabled |

### PresignedUpload

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `url` | `string` | sí | Url |
| `method` | `string` | sí | Method |
| `headers` | `object` | sí | Headers |
| `expires_at` | `string` | sí | Expires At |

### RejectInvoice

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `reason` | `string` | sí | Motiu del rebuig |

### RoleCreate

A new custom role.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Role name |
| `description` | `string` \| `null` | no | What the role is for |

### RoleRead

A custom role.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Role name |
| `description` | `string` \| `null` | no | What the role is for |
| `created_at` | `string` \| `null` | no | Creation time |

### RoleReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`RoleRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### RoleUpdate

Changes to a custom role.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `description` | `string` \| `null` | no | Description |

### StateChange

One change of a record's state.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `at` | `string` | sí | When it happened (UTC) |
| `operation` | `string` | sí | create or transition |
| `machine` | `string` \| `null` | no | Which state machine (resources with several) |
| `event` | `string` \| `null` | no | Transition event; null for create |
| `from_state` | `object` | no | State before; null for create |
| `to_state` | `object` | no | State after |
| `actor` | `string` \| `null` | no | Principal id, system:<job> or null (anonymous) |
| `correlation_id` | `string` \| `null` | no | Shared with logs and events |

### Subjects

Who a policy applies to. Empty means nobody, ``everyone`` any authenticated principal.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `roles` | list[`string`] | no | Application roles |
| `groups` | list[`string`] | no | Identity provider groups |
| `users` | list[`string`] | no | Principal ids (provider:subject) |
| `everyone` | `boolean` | no | Any authenticated principal |

### TagCreate

Dades d'una etiqueta nova.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Nom visible |
| `color` | `string` | no | Color a la interfície |

### TagRead

Representació pública d'una etiqueta.

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `string` | sí | Nom visible |
| `color` | `string` | no | Color a la interfície |
| `id` | `integer` | sí | Id |
| `organization` | `string` \| `null` | no | Organització propietària (tenant); la posa el motor a partir del token |

### TagReadPage

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `items` | list[`TagRead`] | sí | Items |
| `total` | `integer` | sí | Total |
| `offset` | `integer` | sí | Offset |
| `limit` | `integer` | sí | Limit |

### UploadRequest

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `filename` | `string` | sí | Original file name |
| `content_type` | `string` | sí | MIME type |
| `size` | `integer` \| `null` | no | Size in bytes, if known |

### UploadTicketOut

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `document` | `DocumentRead` | sí |  |
| `upload` | `PresignedUpload` | sí |  |

### ValidationError

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `loc` | list[`string` \| `integer`] | sí | Location |
| `msg` | `string` | sí | Message |
| `type` | `string` | sí | Error Type |
| `input` | `object` | no | Input |
| `ctx` | `object` | no | Context |
