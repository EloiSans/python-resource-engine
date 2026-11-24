from __future__ import annotations

from dataclasses import dataclass

from resource_engine.domain import DomainError


class TicketError(DomainError):
    code = "TICKETS"


@dataclass
class Ticket:
    """A support request opened for a customer.

    Business Rules:
        - A ticket has a non-empty subject.
    """

    customer_id: int
    subject: str
    description: str | None = None
    status: str | None = None
    id: int | None = None

    def ensure_valid(self) -> None:
        """Refuse tickets without a subject."""
        if not self.subject.strip():
            raise TicketError("A ticket needs a subject")
