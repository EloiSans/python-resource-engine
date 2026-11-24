from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TicketCreate(BaseModel):
    """Data needed to open a ticket."""

    customer_id: int
    subject: str
    description: str | None = None


class TicketUpdate(BaseModel):
    """Fields of a ticket that can be changed."""

    customer_id: int | None = None
    subject: str | None = None
    description: str | None = None


class TicketRead(BaseModel):
    """A ticket as clients see it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    subject: str
    description: str | None
    status: str
