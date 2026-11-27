# tags

`invoices.application.tags.TagResource` — entitat `Tag`

Etiquetes per classificar factures.

## Àmbit de negoci

Facturació

## Actors

- Emissor de factures

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /tags` |  | TagRead | Llista les etiquetes. |
| `get` | crud | `GET /tags/{id}` |  | TagRead | Consulta una etiqueta. |
| `create` | crud | `POST /tags` | TagCreate | TagRead | Crea una etiqueta. |
| `update` | crud | `PATCH /tags/{id}` | TagCreate | TagRead | Canvia el nom o el color d'una etiqueta. |
| `delete` | crud | `DELETE /tags/{id}` |  |  | Elimina una etiqueta i les seves assignacions. |

Filtres: `name` · Ordenació: `name`

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

Hi fan referència: [invoice_tags.tag_id](invoice_tags.md) (s'eliminen en cascada).

## Relacions molts a molts

| Relació | Registres | A través de | Ruta |
|---|---|---|---|
| `invoices` | [invoices](invoices.md) | [invoice_tags](invoice_tags.md) | `GET /tags/{id}/invoices` |

S'enllacen i es desenllacen creant i eliminant registres del recurs intermedi; `expand` també accepta aquestes relacions.

## Esquemes

### TagCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Nom visible |
| `color` | `str` | no | Color a la interfície |

### TagCreate (update)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Nom visible |
| `color` | `str` | no | Color a la interfície |

### TagRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Nom visible |
| `color` | `str` | no | Color a la interfície |
| `id` | `int` | sí | — |
| `organization` | `str \| None` | no | Organització propietària (tenant); la posa el motor a partir del token |
