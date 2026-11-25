from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    """Dades per donar d'alta un client."""

    name: str = Field(min_length=1, description="Raó social")
    tax_id: str = Field(min_length=3, max_length=20, description="NIF")
    country: str = Field(default="ES", min_length=2, max_length=2, description="País (ISO 3166)")
    email: str | None = Field(default=None, description="Adreça de facturació")


class CustomerUpdate(BaseModel):
    """Camps modificables d'un client."""

    name: str | None = Field(default=None, min_length=1)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    email: str | None = None


class CustomerRead(BaseModel):
    """Representació pública d'un client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tax_id: str
    country: str
    email: str | None


CUSTOMER_ID = "Client registrat (customers); el nom de `customer` es manté per compatibilitat"


class InvoiceCreate(BaseModel):
    """Dades per crear una factura."""

    customer: str = Field(min_length=1, description="Nom del client")
    amount: Decimal = Field(description="Import total")
    issue_date: date | None = Field(default=None, description="Data d'emissió")
    customer_id: int | None = Field(default=None, description=CUSTOMER_ID)
    notes: str | None = None


class InvoiceUpdate(BaseModel):
    """Camps modificables d'una factura en esborrany."""

    customer: str | None = Field(default=None, min_length=1)
    amount: Decimal | None = None
    issue_date: date | None = None
    customer_id: int | None = None
    notes: str | None = None


class InvoiceRead(BaseModel):
    """Representació pública d'una factura."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer: str
    amount: Decimal
    issue_date: date | None
    customer_id: int | None = Field(default=None, description=CUSTOMER_ID)
    notes: str | None
    status: str
    rejection_reason: str | None
    version: int = Field(description="Versió del registre (ETag); envia-la a If-Match")


class RejectInvoice(BaseModel):
    reason: str = Field(min_length=3, description="Motiu del rebuig")


class ApplyDiscount(BaseModel):
    percent: Decimal = Field(gt=0, le=100)
