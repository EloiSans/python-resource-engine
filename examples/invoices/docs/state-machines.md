# Màquines d'estats

Els processos amb estats de cada recurs, dibuixats a partir de la definició executable de la màquina. Els detalls de cada estat i transició són a la pàgina del recurs.

## invoices: approval

Recurs [invoices](resources/invoices.md) — camp `status` — `invoices.domain.state_machine.InvoiceStateMachine`

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

Transicions automàtiques: `reject` després de 30d.

## invoices: delivery

Recurs [invoices](resources/invoices.md) — camp `delivery_status` — `invoices.domain.state_machine.InvoiceDelivery`

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

## invoice_payments: status

Recurs [invoice_payments](resources/invoice_payments.md) — camp `status` — `invoices.domain.state_machine.PaymentFlow`

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

Transicions automàtiques: `expire` després de 15d.

## documents: status

Recurs [documents](resources/documents.md) — camp `status` — `resource_engine.documents.machine.DocumentStateMachine`

Life of a document from upload to deletion.

```mermaid
stateDiagram-v2
    pending: Pending
    processing: Processing
    available: Available
    rejected: Rejected
    deleted: Deleted
    [*] --> pending
    pending --> available: confirm
    pending --> processing: start_processing
    pending --> rejected: reject
    pending --> deleted: discard
    processing --> available: complete
    processing --> rejected: reject
    processing --> deleted: discard
    available --> deleted: discard
    rejected --> deleted: discard
    deleted --> [*]
```

## authz_api_keys: status

Recurs [authz_api_keys](resources/authz_api_keys.md) — camp `status` — `resource_engine.authz.apikeys.ApiKeyStates`

Lifecycle of an API key.

```mermaid
stateDiagram-v2
    active: Active
    revoked: Revoked
    [*] --> active
    active --> revoked: revoke
    revoked --> [*]
```
