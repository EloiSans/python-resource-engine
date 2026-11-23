from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceCreate(BaseModel):
    """Dades per crear una factura."""

    customer: str = Field(min_length=1, description="Nom del client")
    amount: Decimal = Field(description="Import total")
    issue_date: date | None = Field(default=None, description="Data d'emissió")
    notes: str | None = None


class InvoiceUpdate(BaseModel):
    """Camps modificables d'una factura en esborrany."""

    customer: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = None
    issue_date: date | None = None
    notes: str | None = None


class InvoiceRead(BaseModel):
    """Representació pública d'una factura."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer: str
    amount: Decimal
    issue_date: date | None
    notes: str | None
    status: str
    rejection_reason: str | None


class RejectInvoice(BaseModel):
    reason: str = Field(min_length=3, description="Motiu del rebuig")


class ApplyDiscount(BaseModel):
    percent: Decimal = Field(gt=0, le=100)
