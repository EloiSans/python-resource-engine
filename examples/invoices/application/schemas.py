from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

#: Tenant of every record of the module when tenancy is on (bootstrap.build_registry).
ORGANIZATION = "Organització propietària (tenant); la posa el motor a partir del token"


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
    organization: str | None = Field(default=None, description=ORGANIZATION)
    name: str
    tax_id: str
    country: str
    email: str | None


class TagCreate(BaseModel):
    """Dades d'una etiqueta nova."""

    name: str = Field(min_length=1, max_length=50, description="Nom visible")
    color: str = Field(default="grey", max_length=20, description="Color a la interfície")


class TagRead(TagCreate):
    """Representació pública d'una etiqueta."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    organization: str | None = Field(default=None, description=ORGANIZATION)


class InvoiceTagCreate(BaseModel):
    """Etiqueta assignada a una factura."""

    invoice_id: int = Field(description="Factura")
    tag_id: int = Field(description="Etiqueta")


class InvoiceTagRead(InvoiceTagCreate):
    """Assignació d'una etiqueta a una factura."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    organization: str | None = Field(default=None, description=ORGANIZATION)


class PaymentStart(BaseModel):
    """Dades amb què comença el procés de cobrament."""

    invoice_id: int = Field(description="Factura aprovada")
    amount: Decimal = Field(description="Import a cobrar")


class PaymentRead(PaymentStart):
    """Estat del cobrament d'una factura."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str = Field(description="awaiting, overdue o settled")
    status_changed_at: datetime | None = Field(default=None, description="Últim canvi d'estat")
    organization: str | None = Field(default=None, description=ORGANIZATION)


class PaymentReceived(BaseModel):
    """Esdeveniment del servei de pagaments: s'ha cobrat una factura."""

    invoice_id: int
    amount: Decimal


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
    status_changed_at: datetime | None = Field(
        default=None, description="Quan va entrar a l'estat actual (el posa el motor)"
    )
    delivery_status: str = Field(
        default="unsent", description="Estat de l'enviament al client (màquina delivery)"
    )
    version: int = Field(description="Versió del registre (ETag); envia-la a If-Match")
    organization: str | None = Field(default=None, description=ORGANIZATION)


class RejectInvoice(BaseModel):
    reason: str = Field(min_length=3, description="Motiu del rebuig")


class ApplyDiscount(BaseModel):
    percent: Decimal = Field(gt=0, le=100)
