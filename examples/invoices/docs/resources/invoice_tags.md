# invoice_tags

`invoices.application.tags.InvoiceTagResource` — entitat `InvoiceTag`

Etiquetes assignades a factures (molts a molts).

Les factures tenen la relació ``tags`` i les etiquetes la relació
``invoices``; assignar i treure etiquetes és crear i eliminar aquests
registres.

## Business area

Facturació

## Actors

- Emissor de factures

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /invoice_tags` |  | InvoiceTagRead | Llista les assignacions d'etiquetes. |
| `get` | crud | `GET /invoice_tags/{id}` |  | InvoiceTagRead | Consulta una assignació. |
| `create` | crud | `POST /invoice_tags` | InvoiceTagCreate | InvoiceTagRead | Assigna una etiqueta a una factura. |
| `delete` | crud | `DELETE /invoice_tags/{id}` |  |  | Treu una etiqueta d'una factura. |

Filtres: `invoice_id`, `tag_id` · Ordenació: —

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

## Referències

| Relació | Camp | Recurs | En eliminar-lo | Descripció |
|---|---|---|---|---|
| `invoice` | `invoice_id` | [invoices](invoices.md) | s'eliminen en cascada | — |
| `tag` | `tag_id` | [tags](tags.md) | s'eliminen en cascada | — |

Amb `expand` (`?expand=` per HTTP) la resposta inclou el registre relacionat; els filtres `<relació>.<camp>` filtren pels camps filtrables del recurs relacionat.

## Hooks del cicle de vida

| Punt | Hook | Efectes externs | Descripció |
|---|---|---|---|
| `before_create` | `ensure_not_tagged` | no | Refusa assignar a una factura una etiqueta que ja té. |

## Schemas

### InvoiceTagCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `invoice_id` | `int` | sí | Factura |
| `tag_id` | `int` | sí | Etiqueta |

### InvoiceTagRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `invoice_id` | `int` | sí | Factura |
| `tag_id` | `int` | sí | Etiqueta |
| `id` | `int` | sí | — |
| `organization` | `str \| None` | no | Organització propietària (tenant); la posa el motor a partir del token |
