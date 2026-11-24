from __future__ import annotations

from resource_engine import OperationContext, external_effect


def validate(ctx: OperationContext) -> None:
    """Checks the ticket's business rules before it is saved."""
    ctx.entity.ensure_valid()


@external_effect
async def email_customer(ctx: OperationContext) -> None:
    """Tells the customer their ticket was resolved.

    Effects:
        Sends an e-mail through the ``mailer`` service, after the commit.
    """
    if ctx.transition != "resolve":
        return
    ticket = ctx.entity
    await ctx.services["mailer"].send(
        ticket.customer_id,
        f"Ticket {ticket.id} resolved",
        f"Your ticket '{ticket.subject}' has been resolved.",
    )
