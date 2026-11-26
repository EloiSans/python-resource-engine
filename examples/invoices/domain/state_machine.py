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
