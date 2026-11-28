# invoice_payments

Cobrament de les factures aprovades.

Comença quan s'aprova una factura, espera l'esdeveniment ``PaymentReceived``
del servei de pagaments i aleshores marca la factura com a cobrada. Si en 15
dies no arriba, passa a endarrerida i ho anuncia; un cobrament posterior el
tanca igualment.

## Àmbit de negoci

Facturació

## Actors

- Servei de pagaments

## Operacions

| Operació | Tipus | Descripció |
|---|---|---|
| `list` | crud | Llista els cobraments en curs i acabats. |
| `get` | crud | Consulta el cobrament d'una factura. |
| `create` | crud | El crea el procés quan s'aprova una factura. |
| `receive` | transition | El servei de pagaments ha cobrat la factura. |
| `expire` | transition | Han passat 15 dies sense cobrament. |

Multi-inquilí: cada registre pertany a l'inquilí del camp `organization`; les operacions es limiten a l'inquilí de qui crida.

Auditoria: s'auditen els canvis i els intents denegats.

## Màquina d'estats

Cobrament d'una factura aprovada.

```mermaid
stateDiagram-v2
    awaiting: Pendent de cobrament
    overdue: Endarrerida
    settled: Cobrada
    [*] --> awaiting
    awaiting --> settled: receive
    awaiting --> overdue: expire
    overdue --> settled: receive
    settled --> [*]
```

| Estat | Nom | Descripció |
|---|---|---|
| `awaiting` (inicial) | Pendent de cobrament | S'espera el cobrament del servei de pagaments. |
| `overdue` | Endarrerida | Han passat 15 dies sense cobrament; encara es pot cobrar. |
| `settled` (final) | Cobrada | Cobrada. Estat final. |

| Transició | Origen | Destí | Descripció |
|---|---|---|---|
| `receive` | `awaiting` | `settled` | El servei de pagaments ha cobrat la factura. |
| `expire` | `awaiting` | `overdue` | Han passat 15 dies sense cobrament. |
| `receive` | `overdue` | `settled` | El servei de pagaments ha cobrat la factura. |

### Transicions automàtiques

| Transició | Després de | Origen | Comptat des de | Descripció |
|---|---|---|---|---|
| `expire` | 15d | `awaiting` | `status_changed_at` | Sense cobrament en 15 dies, el cobrament queda endarrerit. |

Les executa el treballador (`TimerJob`) com a principal del sistema; queden a l'historial i a l'auditoria com qualsevol transició.

## Procés

Cada registre és una instància del procés, una per valor de `invoice_id`. Reacciona a aquests esdeveniments:

| Esdeveniment | Quan | Clau | Inicia | Què fa |
|---|---|---|---|---|
| `TransitionExecuted` | resource invoices, transition approve | `entity_id` | sí | Quan s'aprova una factura, comença a esperar-ne el cobrament. |
| `PaymentReceived` | — | `invoice_id` | no | Un cobrament del servei de pagaments tanca el procés i marca la factura com a cobrada. |

## Referències

| Relació | Camp | Recurs | En eliminar-lo | Descripció |
|---|---|---|---|---|
| `invoice` | `invoice_id` | [invoices](invoices.md) | es denega mentre hi hagi referències | — |

## Esdeveniments de domini

### `PaymentOverdue`

Una factura aprovada fa 15 dies que espera el cobrament.

| Camp | Tipus | Obligatori |
|---|---|---|
| `invoice_id` | `int` | sí |

## Què passa en cada operació

Els passos **abans de confirmar** són dins la transacció: si un falla, el canvi es rebutja i no es desa res. Els passos **després de confirmar** s'executen amb el canvi ja desat: un error queda registrat, però no el desfà.

### Transició `expire`

1. Es valida l'entrada.
2. Canvia l'estat.
3. Abans de confirmar: Emet ``PaymentOverdue`` perquè altres serveis (recordatoris) en tinguin notícia.
4. Es confirma el canvi, amb l'auditoria i els esdeveniments.

```mermaid
flowchart LR
    s0["Validar"]
    s1["Canviar l'estat"]
    s0 --> s1
    s2["Emet ``PaymentOverdue`` perquè altres s…"]
    s1 --> s2
    s3{{"Confirmar"}}
    s2 --> s3
```
