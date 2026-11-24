from __future__ import annotations

from helpdesk.tickets.application.schemas import ReassignInput
from resource_engine import Action, OperationContext


class Reassign(Action):
    """Gives the ticket to another agent.

    Postconditions:
        The ticket's ``assignee`` is the agent given.
    """

    input_schema = ReassignInput

    async def execute(self, ctx: OperationContext) -> None:
        ticket = ctx.entity
        ticket.assignee = ctx.data.assignee
        await ctx.repository().update(ticket)
