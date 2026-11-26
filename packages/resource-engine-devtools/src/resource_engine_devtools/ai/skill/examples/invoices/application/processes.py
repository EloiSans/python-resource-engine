from __future__ import annotations

from datetime import timedelta

from resource_engine import OperationContext, Reference, Timer
from resource_engine.processes import On, Process, ProcessStep

from invoices.application.schemas import PaymentRead, PaymentReceived, PaymentStart
from invoices.domain.entities import InvoicePayment
from invoices.domain.events import PaymentOverdue
from invoices.domain.state_machine import PaymentFlow


async def start_payment(step: ProcessStep) -> None:
    """Quan s'aprova una factura, comença a esperar-ne el cobrament."""
    invoice = await step.manager.get("invoices", step.key, actor=step.actor)
    await step.start({"amount": invoice.amount}, tenant=getattr(invoice, "organization", None))


async def record_payment(step: ProcessStep) -> None:
    """Un cobrament del servei de pagaments tanca el procés i marca la factura com a cobrada."""
    if not step.can("receive"):
        return  # already settled: a repeated notification
    await step.transition("receive")
    await step.manager.transition("invoices", step.key, "pay", actor=step.actor)


def announce_overdue(ctx: OperationContext) -> None:
    """Emet ``PaymentOverdue`` perquè altres serveis (recordatoris) en tinguin notícia."""
    ctx.record_event(PaymentOverdue(invoice_id=ctx.entity.invoice_id))


class InvoicePaymentProcess(Process):
    """Cobrament de les factures aprovades.

    Comença quan s'aprova una factura, espera l'esdeveniment ``PaymentReceived``
    del servei de pagaments i aleshores marca la factura com a cobrada. Si en 15
    dies no arriba, passa a endarrerida i ho anuncia; un cobrament posterior el
    tanca igualment.

    Business area:
        Facturació

    Actors:
        - Servei de pagaments
    """

    name = "invoice_payments"
    model = InvoicePayment
    schemas = {"create": PaymentStart, "read": PaymentRead}
    operations = ["list", "get", "create"]
    operation_docs = {
        "list": "Llista els cobraments en curs i acabats.",
        "get": "Consulta el cobrament d'una factura.",
        "create": "El crea el procés quan s'aprova una factura.",
    }
    state_machine = PaymentFlow
    state_since_field = "status_changed_at"
    timers = [
        Timer(
            "expire",
            after=timedelta(days=15),
            description="Sense cobrament en 15 dies, el cobrament queda endarrerit.",
        )
    ]
    hooks = {"after_transition:expire": [announce_overdue]}
    events = [PaymentOverdue]
    references = {"invoice_id": Reference("invoices", related_name="payments")}
    correlation = "invoice_id"
    reacts = [
        On(
            "TransitionExecuted",
            start_payment,
            resource="invoices",
            transition="approve",
            start=True,
        ),
        On("PaymentReceived", record_payment, key="invoice_id", payload=PaymentReceived),
    ]
    filterable = ["status", "invoice_id"]
    sortable = ["id"]
