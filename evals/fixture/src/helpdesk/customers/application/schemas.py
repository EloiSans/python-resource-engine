from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CustomerCreate(BaseModel):
    """Data needed to create a customer."""

    name: str
    email: str | None = None


class CustomerUpdate(BaseModel):
    """Fields of a customer that can be changed."""

    name: str | None = None
    email: str | None = None


class CustomerRead(BaseModel):
    """A customer as clients see it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
