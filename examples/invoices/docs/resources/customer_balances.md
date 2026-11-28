# customer_balances

Saldo de facturació de cada client, calculat a partir de les factures.

És una vista: només es consulta, i cada fila es recalcula amb les factures
del client (no es desa enlloc).

## Àmbit de negoci

Facturació

## Actors

- Administrador
- Emissor de factures

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Llista els saldos dels clients amb factures, ordenables per import. |
| `get` | crud | Consulta el saldo d'un client. |

Vista de només lectura calculada a partir de [invoices](invoices.md), amb una fila per `customer_id`, `organization`:

| Camp | Càlcul |
|---|---|
| `invoices` | `count()` |
| `total` | `sum(amount)` |
| `outstanding` | `sum(amount) where status=['pending', 'approved']` |
| `paid` | `sum(amount) where status=paid` |
| `last_issue_date` | `max(issue_date)` |

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

## Referències

| Relació | Camp | Recurs | En eliminar-lo | Descripció |
|---|---|---|---|---|
| `customer` | `customer_id` | [customers](customers.md) | es denega mentre hi hagi referències | El client. |
