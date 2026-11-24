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
