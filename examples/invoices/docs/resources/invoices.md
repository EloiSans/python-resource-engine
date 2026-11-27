# invoices

`invoices.application.resource.InvoiceResource` — entitat `Invoice`

Gestió de factures de l'organització.

Permet crear, consultar i modificar factures, així com gestionar-ne el
procés d'aprovació i cobrament.

## Àmbit de negoci

Facturació

## Actors

- Administrador
- Comptable
- Responsable

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /invoices` |  | InvoiceRead | Llista les factures, filtrables per estat i client. |
| `get` | crud | `GET /invoices/{id}` |  | InvoiceRead | Consulta una factura. |
| `create` | crud | `POST /invoices` | InvoiceCreate | InvoiceRead | Crea una factura en estat esborrany. |
| `update` | crud | `PATCH /invoices/{id}` | InvoiceUpdate | InvoiceRead | Modifica una factura mentre és un esborrany. |
| `delete` | crud | `DELETE /invoices/{id}` |  |  | Elimina una factura en esborrany. |
| `submit` | transition | `POST /invoices/{id}/transitions/submit` |  | InvoiceRead | Envia l'esborrany a aprovació. |
| `approve` | transition | `POST /invoices/{id}/transitions/approve` |  | InvoiceRead | Aprova una factura pendent. |
| `reject` | transition | `POST /invoices/{id}/transitions/reject` | RejectInvoice | InvoiceRead | Rebutja una factura pendent indicant-ne el motiu. |
| `pay` | transition | `POST /invoices/{id}/transitions/pay` |  | InvoiceRead | Registra el cobrament d'una factura aprovada. |
| `reopen` | transition | `POST /invoices/{id}/transitions/reopen` |  | InvoiceRead | Torna una factura rebutjada a esborrany. |
| `send` | transition | `POST /invoices/{id}/transitions/send` |  | InvoiceRead | Envia al client una factura aprovada o cobrada. |
| `bounce` | transition | `POST /invoices/{id}/transitions/bounce` |  | InvoiceRead | El correu d'enviament ha rebotat. |
| `resend` | transition | `POST /invoices/{id}/transitions/resend` |  | InvoiceRead | Torna a enviar una factura retornada. |
| `recalculate_totals` | action | `POST /invoices/{id}/actions/recalculate_totals` | ApplyDiscount |  | Aplica un descompte percentual a l'import d'una factura. |
| `render_summary` | action | `POST /invoices/{id}/actions/render_summary` |  |  | Genera un resum en Markdown de la factura i l'adjunta a la ranura ``summary``. |

Filtres: `status`, `delivery_status`, `customer`, `customer_id` · Ordenació: `id`, `amount`, `issue_date`

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Concurrència optimista: el camp `version` és la versió del registre (capçalera `ETag`); les modificacions envien `If-Match` i una versió antiga es rebutja amb 409.

Camps restringits: `notes` — cal una concessió explícita (`invoices:read:<camp>`, `invoices:write:<camp>`); si no, s'ometen de les respostes.

Auditoria: s'auditen els canvis i els intents denegats; sense valors per a `notes`.

## Màquina d'estats `approval`

`invoices.domain.state_machine.InvoiceStateMachine` — camp `status`

Procés d'aprovació i cobrament d'una factura.

```mermaid
stateDiagram-v2
    draft: Esborrany
    pending: Pendent
    approved: Aprovada
    rejected: Rebutjada
    paid: Cobrada
    [*] --> draft
    draft --> pending: submit
    pending --> approved: approve
    pending --> rejected: reject
    approved --> paid: pay
    rejected --> draft: reopen
    paid --> [*]
```

| Estat | Nom | Descripció |
|---|---|---|
| `draft` (inicial) | Esborrany | Esborrany editable per l'emissor. |
| `pending` | Pendent | Enviada a aprovació; ja no es pot modificar. |
| `approved` | Aprovada | Aprovada i pendent de cobrament. |
| `rejected` | Rebutjada | Rebutjada pel responsable. |
| `paid` (final) | Cobrada | Cobrada. Estat final. |

| Transició | Origen | Destí | Descripció |
|---|---|---|---|
| `submit` | `draft` | `pending` | Envia l'esborrany a aprovació. |
| `approve` | `pending` | `approved` | Aprova una factura pendent. |
| `reject` | `pending` | `rejected` | Rebutja una factura pendent indicant-ne el motiu. |
| `pay` | `approved` | `paid` | Registra el cobrament d'una factura aprovada. |
| `reopen` | `rejected` | `draft` | Torna una factura rebutjada a esborrany. |

## Màquina d'estats `delivery`

`invoices.domain.state_machine.InvoiceDelivery` — camp `delivery_status`

Enviament d'una factura al client, independent de la seva aprovació.

```mermaid
stateDiagram-v2
    unsent: No enviada
    sent: Enviada
    returned: Retornada
    [*] --> unsent
    unsent --> sent: send
    sent --> returned: bounce
    returned --> sent: resend
```

| Estat | Nom | Descripció |
|---|---|---|
| `unsent` (inicial) | No enviada | Encara no s'ha enviat al client. |
| `sent` | Enviada | Enviada al client. |
| `returned` | Retornada | El correu ha rebotat; cal tornar-la a enviar. |

| Transició | Origen | Destí | Descripció |
|---|---|---|---|
| `send` | `unsent` | `sent` | Envia al client una factura aprovada o cobrada. |
| `bounce` | `sent` | `returned` | El correu d'enviament ha rebotat. |
| `resend` | `returned` | `sent` | Torna a enviar una factura retornada. |

### Transicions automàtiques

| Transició | Després de | Origen | Comptat des de | Descripció |
|---|---|---|---|---|
| `reject` | 30d | `pending` | `status_changed_at` | Les factures pendents d'aprovar més de 30 dies es rebutgen soles. |

Les executa el treballador (`TimerJob`) com a principal del sistema; queden a l'historial i a l'auditoria com qualsevol transició.

## Accions

### `recalculate_totals`

`invoices.application.actions.RecalculateTotals`

Aplica un descompte percentual a l'import d'una factura.

#### Precondicions

La factura és un esborrany.

#### Postcondicions

L'import queda reduït pel percentatge indicat, arrodonit a cèntims.

#### Efectes

Emet l'esdeveniment ``InvoiceTotalsRecalculated``.

### `render_summary`

`invoices.application.actions.RenderSummary`

Genera un resum en Markdown de la factura i l'adjunta a la ranura ``summary``.

#### Postcondicions

La factura té un únic document ``summary`` amb les dades actuals.

#### Efectes

Desa el document al magatzem de fitxers i emet ``DocumentAttached``.

## Referències

| Relació | Camp | Recurs | En eliminar-lo | Descripció |
|---|---|---|---|---|
| `customer_record` | `customer_id` | [customers](customers.md) | es denega mentre hi hagi referències | Client registrat al qual s'emet la factura. |

Amb `expand` (`?expand=` per HTTP) la resposta inclou el registre relacionat; els filtres `<relació>.<camp>` filtren pels camps filtrables del recurs relacionat.

Hi fan referència: [invoice_tags.invoice_id](invoice_tags.md) (s'eliminen en cascada), [invoice_payments.invoice_id](invoice_payments.md) (es denega mentre hi hagi referències).

| Relació inversa | Registres | Ruta | `expand` |
|---|---|---|---|
| `payments` | [invoice_payments](invoice_payments.md) | `GET /invoices/{id}/payments` | `payments` o `payments:25` (una pàgina) |

## Relacions molts a molts

| Relació | Registres | A través de | Ruta |
|---|---|---|---|
| `tags` | [tags](tags.md) | [invoice_tags](invoice_tags.md) | `GET /invoices/{id}/tags` |

S'enllacen i es desenllacen creant i eliminant registres del recurs intermedi; `expand` també accepta aquestes relacions.

## Esdeveniments de domini

### `InvoiceTotalsRecalculated`

`invoices.domain.events.InvoiceTotalsRecalculated`

L'import d'una factura s'ha recalculat aplicant un descompte.

| Camp | Tipus | Obligatori |
|---|---|---|
| `invoice_id` | `int` | sí |
| `previous_amount` | `Decimal` | sí |
| `new_amount` | `Decimal` | sí |

## Documents

| Ranura | Tipus | Mida màx. | Múltiple | Pujada | Requerit per | Processament | Descripció |
|---|---|---|---|---|---|---|---|
| `signed_pdf` | application/pdf | 10485760 B | no | any | `approve` | no | Factura signada pel client; cal per aprovar-la. |
| `attachments` | */* | 26214400 B | sí | any | — | no | Justificants i altra documentació de suport. |
| `summary` | text/markdown | 26214400 B | no | api | — | no | Resum generat per l'acció render_summary. |

## Correus

| Correu | Quan | Destinataris | Idioma |
|---|---|---|---|
| [`invoice_approved`](../emails.md#invoice_approved) | en la transició `approve` (`request_approval_email`) | `[customer.email]` | ca |

Es demanen dins la transacció i s'envien quan el canvi s'ha confirmat; si el canvi es desfà, no s'envien.

## Què passa en cada operació

Els passos **abans de confirmar** són dins la transacció: si un falla, el canvi es rebutja i no es desa res. Els passos **després de confirmar** s'executen amb el canvi ja desat: un error queda registrat, però no el desfà.

### Crear (`create`)

1. Es valida l'entrada (`InvoiceCreate`).
2. Abans de desar: `validate_amount` — Rebutja factures amb import negatiu.
3. Es desa el registre nou.
4. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["Validar"]
    s1["validate_amount"]
    s0 --> s1
    s2["Desar"]
    s1 --> s2
    s3{{"Confirmar"}}
    s2 --> s3
```

### Modificar (`update`)

1. Es valida l'entrada (`InvoiceUpdate`).
2. Abans de desar: `ensure_editable` — Només es poden modificar o eliminar factures en esborrany.
3. Es desen els canvis.
4. Abans de confirmar: `validate_amount` — Rebutja factures amb import negatiu.
5. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["Validar"]
    s1["ensure_editable"]
    s0 --> s1
    s2["Desar"]
    s1 --> s2
    s3["validate_amount"]
    s2 --> s3
    s4{{"Confirmar"}}
    s3 --> s4
```

### Eliminar (`delete`)

1. Abans de desar: `ensure_editable` — Només es poden modificar o eliminar factures en esborrany.
2. S'elimina el registre.
3. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["ensure_editable"]
    s1["Eliminar"]
    s0 --> s1
    s2{{"Confirmar"}}
    s1 --> s2
```

### Transició `approve`

1. Es valida l'entrada.
2. Cal un document disponible a `signed_pdf`.
3. Canvia l'estat.
4. Abans de confirmar: `request_approval_email` — Demana el correu d'aprovació per al client registrat, si té adreça.
5. Es confirma el canvi, amb l'auditoria i els esdeveniments.
6. Després de confirmar: s'envia el correu [`invoice_approved`](../emails.md#invoice_approved) a `[customer.email]`.

```mermaid
flowchart LR
    s0["Validar"]
    s1["Document signed_pdf"]
    s0 --> s1
    s2["Canviar l'estat"]
    s1 --> s2
    s3["request_approval_email"]
    s2 --> s3
    s4{{"Confirmar"}}
    s3 --> s4
    s5["Correu invoice_approved"]
    s4 --> s5
```

Després de qualsevol canvi confirmat d'aquest recurs:

- `notify_approval` (efecte extern) — Notifica el client que la factura ha estat aprovada.

| Punt | Hook | Efectes externs | Descripció |
|---|---|---|---|
| `before_create` | `validate_amount` | no | Rebutja factures amb import negatiu. |
| `before_update` | `ensure_editable` | no | Només es poden modificar o eliminar factures en esborrany. |
| `after_update` | `validate_amount` | no | Rebutja factures amb import negatiu. |
| `before_delete` | `ensure_editable` | no | Només es poden modificar o eliminar factures en esborrany. |
| `after_transition:approve` | `request_approval_email` | no | Demana el correu d'aprovació per al client registrat, si té adreça. |
| `after_commit` | `notify_approval` | sí | Notifica el client que la factura ha estat aprovada. |

## Esquemes

### InvoiceCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer` | `str` | sí | Nom del client |
| `amount` | `Decimal` | sí | Import total |
| `issue_date` | `date \| None` | no | Data d'emissió |
| `customer_id` | `int \| None` | no | Client registrat (customers); el nom de `customer` es manté per compatibilitat |
| `notes` | `str \| None` | no | — |

### InvoiceUpdate (update)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer` | `str \| None` | no | — |
| `amount` | `Decimal \| None` | no | — |
| `issue_date` | `date \| None` | no | — |
| `customer_id` | `int \| None` | no | — |
| `notes` | `str \| None` | no | — |

### InvoiceRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `int` | sí | — |
| `customer` | `str` | sí | — |
| `amount` | `Decimal` | sí | — |
| `issue_date` | `date \| None` | sí | — |
| `customer_id` | `int \| None` | no | Client registrat (customers); el nom de `customer` es manté per compatibilitat |
| `notes` | `str \| None` | sí | — |
| `status` | `str` | sí | — |
| `rejection_reason` | `str \| None` | sí | — |
| `status_changed_at` | `datetime \| None` | no | Quan va entrar a l'estat actual (el posa el motor) |
| `delivery_status` | `str` | no | Estat de l'enviament al client (màquina delivery) |
| `version` | `int` | sí | Versió del registre (ETag); envia-la a If-Match |
| `organization` | `str \| None` | no | Organització propietària (tenant); la posa el motor a partir del token |
