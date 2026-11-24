from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from resource_engine import Action, OperationContext
from resource_engine.documents import NewDocument

from invoices.application.schemas import ApplyDiscount
from invoices.domain.events import InvoiceTotalsRecalculated


class RecalculateTotals(Action):
    """Aplica un descompte percentual a l'import d'una factura.

    Preconditions:
        La factura és un esborrany.

    Postconditions:
        L'import queda reduït pel percentatge indicat, arrodonit a cèntims.

    Effects:
        Emet l'esdeveniment ``InvoiceTotalsRecalculated``.
    """

    input_schema = ApplyDiscount

    async def execute(self, ctx: OperationContext) -> None:
        invoice = ctx.entity
        invoice.ensure_editable()
        previous = invoice.amount
        factor = (Decimal(100) - ctx.data.percent) / Decimal(100)
        invoice.amount = (previous * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        await ctx.repository().update(invoice)
        ctx.record_event(
            InvoiceTotalsRecalculated(
                invoice_id=invoice.id, previous_amount=previous, new_amount=invoice.amount
            )
        )


class RenderSummary(Action):
    """Genera un resum en Markdown de la factura i l'adjunta a la ranura ``summary``.

    Postconditions:
        La factura té un únic document ``summary`` amb les dades actuals.

    Effects:
        Desa el document al magatzem de fitxers i emet ``DocumentAttached``.
    """

    async def execute(self, ctx: OperationContext) -> None:
        invoice = ctx.entity
        text = (
            f"# Factura {invoice.id}\n\n"
            f"- Client: {invoice.customer}\n"
            f"- Import: {invoice.amount}\n"
            f"- Estat: {invoice.status}\n"
        )
        await ctx.documents.attach(
            NewDocument("summary", text.encode(), f"factura-{invoice.id}.md", "text/markdown")
        )
