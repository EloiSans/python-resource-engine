# tags

Etiquetes per classificar factures.

## Àmbit de negoci

Facturació

## Actors

- Emissor de factures

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Llista les etiquetes. |
| `get` | crud | Consulta una etiqueta. |
| `create` | crud | Crea una etiqueta. |
| `update` | crud | Canvia el nom o el color d'una etiqueta. |
| `delete` | crud | Elimina una etiqueta i les seves assignacions. |

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

Hi fan referència: [invoice_tags.tag_id](invoice_tags.md) (s'eliminen en cascada).

## Relacions molts a molts

| Relació | Registres | A través de |
|---|---|---|
| `invoices` | [invoices](invoices.md) | [invoice_tags](invoice_tags.md) |

S'enllacen i es desenllacen creant i eliminant registres del recurs intermedi; `expand` també accepta aquestes relacions.
