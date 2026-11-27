# customer_balances

`invoices.application.balances.CustomerBalanceResource` — entitat `CustomerBalance`

Saldo de facturació de cada client, calculat a partir de les factures.

És una vista: només es consulta, i cada fila es recalcula amb les factures
del client (no es desa enlloc).

## Àmbit de negoci

Facturació

## Actors

- Administrador
- Emissor de factures

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /customer_balances` |  | CustomerBalanceRead | Llista els saldos dels clients amb factures, ordenables per import. |
| `get` | crud | `GET /customer_balances/{id}` |  | CustomerBalanceRead | Consulta el saldo d'un client. |

Vista de només lectura calculada a partir de [invoices](invoices.md), amb una fila per `customer_id`, `organization`:

| Camp | Càlcul |
|---|---|
| `invoices` | `count()` |
| `total` | `sum(amount)` |
| `outstanding` | `sum(amount) where status=['pending', 'approved']` |
| `paid` | `sum(amount) where status=paid` |
| `last_issue_date` | `max(issue_date)` |

Filtres: `customer_id` · Ordenació: `total`, `outstanding`, `paid`, `last_issue_date`

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

## Referències

| Relació | Camp | Recurs | En eliminar-lo | Descripció |
|---|---|---|---|---|
| `customer` | `customer_id` | [customers](customers.md) | es denega mentre hi hagi referències | El client. |

Amb `expand` (`?expand=` per HTTP) la resposta inclou el registre relacionat; els filtres `<relació>.<camp>` filtren pels camps filtrables del recurs relacionat.

## Esquemes

### CustomerBalanceRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `customer_id` | `int` | sí | Client (customers) |
| `organization` | `str \| None` | no | Organització propietària (tenant); la posa el motor a partir del token |
| `invoices` | `int` | sí | Nombre de factures |
| `total` | `Decimal` | sí | Import total de les factures |
| `outstanding` | `Decimal` | sí | Import pendent d'aprovar o de cobrar |
| `paid` | `Decimal` | sí | Import cobrat |
| `last_issue_date` | `date \| None` | sí | Data d'emissió més recent |
