from __future__ import annotations

from statemachine import State, StateMachine


class TicketStateMachine(StateMachine):
    """How a ticket moves from opening to resolution.

    States:
        open: Waiting for an agent.
        in_progress: An agent is working on it.
        resolved: The problem is solved. Final.
        cancelled: Closed without a resolution. Final.

    Transitions:
        start: An agent takes the ticket.
        resolve: The agent solves it.
        cancel: A supervisor closes it without a resolution.
    """

    open = State("Open", initial=True)
    in_progress = State("InProgress")
    resolved = State("Resolved", final=True)
    cancelled = State("Cancelled", final=True)

    start = open.to(in_progress)
    resolve = in_progress.to(resolved)
    cancel = open.to(cancelled) | in_progress.to(cancelled)
