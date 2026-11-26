from __future__ import annotations

from statemachine import State, StateMachine


class InvoiceStateMachine(StateMachine):
    """Procés d'aprovació i cobrament d'una factura.

    States:
        draft: Esborrany editable per l'emissor.
        pending: Enviada a aprovació; ja no es pot modificar.
        approved: Aprovada i pendent de cobrament.
        rejected: Rebutjada pel responsable.
        paid: Cobrada. Estat final.

    Transitions:
        submit: Envia l'esborrany a aprovació.
        approve: Aprova una factura pendent.
        reject: Rebutja una factura pendent indicant-ne el motiu.
        reopen: Torna una factura rebutjada a esborrany.
        pay: Registra el cobrament d'una factura aprovada.
    """

    draft = State("Esborrany", initial=True)
    pending = State("Pendent")
    approved = State("Aprovada")
    rejected = State("Rebutjada")
    paid = State("Cobrada", final=True)

    submit = draft.to(pending)
    approve = pending.to(approved)
    reject = pending.to(rejected)
    reopen = rejected.to(draft)
    pay = approved.to(paid)

    def on_reject(self, reason: str) -> None:
        self.model.rejection_reason = reason

    def on_reopen(self) -> None:
        self.model.rejection_reason = None


class InvoiceDelivery(StateMachine):
    """Enviament d'una factura al client, independent de la seva aprovació.

    States:
        unsent: Encara no s'ha enviat al client.
        sent: Enviada al client.
        returned: El correu ha rebotat; cal tornar-la a enviar.

    Transitions:
        send: Envia al client una factura aprovada o cobrada.
        bounce: El correu d'enviament ha rebotat.
        resend: Torna a enviar una factura retornada.
    """

    unsent = State("No enviada", initial=True)
    sent = State("Enviada")
    returned = State("Retornada")

    send = unsent.to(sent, cond="invoice_approved")
    bounce = sent.to(returned)
    resend = returned.to(sent)

    def invoice_approved(self) -> bool:
        # a guard can read the other machine's state: only approved invoices go out
        return self.model.status in ("approved", "paid")


class PaymentFlow(StateMachine):
    """Cobrament d'una factura aprovada.

    States:
        awaiting: S'espera el cobrament del servei de pagaments.
        overdue: Han passat 15 dies sense cobrament; encara es pot cobrar.
        settled: Cobrada. Estat final.

    Transitions:
        receive: El servei de pagaments ha cobrat la factura.
        expire: Han passat 15 dies sense cobrament.
    """

    awaiting = State("Pendent de cobrament", initial=True)
    overdue = State("Endarrerida")
    settled = State("Cobrada", final=True)

    receive = awaiting.to(settled) | overdue.to(settled)
    expire = awaiting.to(overdue)
