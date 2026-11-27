from __future__ import annotations

import logging

from resource_engine import OperationContext, external_effect
from resource_engine.email import EmailRequested

logger = logging.getLogger(__name__)


def validate_amount(ctx: OperationContext) -> None:
    """Rebutja factures amb import negatiu."""
    ctx.entity.ensure_valid_amount()


def ensure_editable(ctx: OperationContext) -> None:
    """Només es poden modificar o eliminar factures en esborrany."""
    ctx.entity.ensure_editable()


async def request_approval_email(ctx: OperationContext) -> None:
    """Demana el correu d'aprovació per al client registrat, si té adreça.

    El correu es desa amb el canvi (outbox) i el worker l'envia quan la
    transacció s'ha confirmat.
    """
    invoice = ctx.entity
    if invoice.customer_id is None:
        return
    customer = await ctx.repository("customers").get(invoice.customer_id)
    if customer is None or not customer.email:
        return
    ctx.record_event(
        EmailRequested(
            template="invoice_approved",
            to=[customer.email],
            context={"number": invoice.id, "customer": customer.name, "amount": invoice.amount},
            locale="ca",
            resource="invoices",
            entity_id=invoice.id,
        )
    )


@external_effect
async def notify_approval(ctx: OperationContext) -> None:
    """Notifica el client que la factura ha estat aprovada.

    Effects:
        Envia una notificació mitjançant el servei ``notifier``.
    """
    if ctx.transition != "approve":
        return
    notifier = ctx.services.get("notifier")
    if notifier is not None:
        await notifier.send(f"Invoice {ctx.entity_id} approved")
