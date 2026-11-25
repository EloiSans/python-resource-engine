# customers

`invoices.application.customers.CustomerResource` — entitat `Customer`

Clients als quals l'organització emet factures.

Les factures hi fan referència amb ``customer_id``; un client amb factures
no es pot eliminar.

## Business area

Facturació

## Actors

- Administrador
- Emissor de factures

## Operacions

| Operació | Tipus | Endpoint | Entrada | Sortida | Descripció |
|---|---|---|---|---|---|
| `list` | crud | `GET /customers` |  | CustomerRead | Llista els clients, filtrables per país i nom. |
| `get` | crud | `GET /customers/{id}` |  | CustomerRead | Consulta un client. |
| `create` | crud | `POST /customers` | CustomerCreate | CustomerRead | Dona d'alta un client. |
| `update` | crud | `PATCH /customers/{id}` | CustomerUpdate | CustomerRead | Modifica les dades d'un client. |
| `delete` | crud | `DELETE /customers/{id}` |  |  | Elimina un client que no té factures. |

Filtres: `country`, `name`, `tax_id` · Ordenació: `id`, `name`

Auditoria: s'auditen els canvis i els intents denegats.

Hi fan referència: [invoices.customer_id](invoices.md) (es denega mentre hi hagi referències).

| Relació inversa | Registres | Ruta | `expand` |
|---|---|---|---|
| `invoices` | [invoices](invoices.md) | `GET /customers/{id}/invoices` | `invoices` o `invoices:25` (una pàgina) |

## Schemas

### CustomerCreate (create)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str` | sí | Raó social |
| `tax_id` | `str` | sí | NIF |
| `country` | `str` | no | País (ISO 3166) |
| `email` | `str \| None` | no | Adreça de facturació |

### CustomerUpdate (update)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `name` | `str \| None` | no | — |
| `country` | `str \| None` | no | — |
| `email` | `str \| None` | no | — |

### CustomerRead (read)

| Camp | Tipus | Obligatori | Descripció |
|---|---|---|---|
| `id` | `int` | sí | — |
| `name` | `str` | sí | — |
| `tax_id` | `str` | sí | — |
| `country` | `str` | sí | — |
| `email` | `str \| None` | sí | — |
