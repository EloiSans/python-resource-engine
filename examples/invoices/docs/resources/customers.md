# customers

Clients als quals l'organització emet factures.

Les factures hi fan referència amb ``customer_id``; un client amb factures
no es pot eliminar.

## Àmbit de negoci

Facturació

## Actors

- Administrador
- Emissor de factures

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Llista els clients, filtrables per país i nom. |
| `get` | crud | Consulta un client. |
| `create` | crud | Dona d'alta un client. |
| `update` | crud | Modifica les dades d'un client. |
| `delete` | crud | Elimina un client que no té factures. |

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

Hi fan referència: [invoices.customer_id](invoices.md) (es denega mentre hi hagi referències), [customer_balances.customer_id](customer_balances.md) (es denega mentre hi hagi referències).
