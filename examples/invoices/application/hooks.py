from __future__ import annotations

import logging

from resource_engine import OperationContext, external_effect

logger = logging.getLogger(__name__)


def validate_amount(ctx: OperationContext) -> None:
    """Rebutja factures amb import negatiu."""
    ctx.entity.ensure_valid_amount()


def ensure_editable(ctx: OperationContext) -> None:
    """Només es poden modificar o eliminar factures en esborrany."""
    ctx.entity.ensure_editable()


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
