from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from resource_engine.domain import DomainError


class InvoiceError(DomainError):
    code = "INVOICE"


@dataclass
class Customer:
    """Client de l'organització al qual s'emeten factures.

    Business Rules:
        - El NIF identifica el client; no es pot repetir.
        - No es pot eliminar un client que encara té factures.
    """

    name: str
    tax_id: str
    country: str = "ES"
    email: str | None = None
    organization: str | None = None
    id: int | None = field(default=None)


@dataclass
class Tag:
    """Etiqueta per classificar factures (urgent, revisar, client nou...)."""

    name: str
    color: str = "grey"
    organization: str | None = None
    id: int | None = field(default=None)


@dataclass
class InvoiceTag:
    """Assignació d'una etiqueta a una factura.

    Business Rules:
        - Una factura no pot tenir la mateixa etiqueta dues vegades.
    """

    invoice_id: int
    tag_id: int
    organization: str | None = None
    id: int | None = field(default=None)


@dataclass
class InvoicePayment:
    """Procés de cobrament d'una factura aprovada (una instància per factura).

    Business Rules:
        - Comença quan s'aprova la factura i acaba quan se'n rep el cobrament.
    """

    invoice_id: int
    amount: Decimal
    status: str | None = None
    status_changed_at: datetime | None = None
    organization: str | None = None
    id: int | None = field(default=None)


@dataclass
class Invoice:
    """Factura emesa a un client.

    Business Rules:
        - L'import total no pot ser negatiu.
        - Una factura només es pot modificar mentre és un esborrany.
    """

    customer: str
    amount: Decimal
    issue_date: date | None = None
    customer_id: int | None = None
    notes: str | None = None
    status: str | None = None
    rejection_reason: str | None = None
    status_changed_at: datetime | None = None
    delivery_status: str | None = None
    version: int | None = None
    organization: str | None = None
    id: int | None = field(default=None)

    def ensure_editable(self) -> None:
        if self.status not in (None, "draft"):
            raise InvoiceError(f"Invoice {self.id} is {self.status} and can no longer be modified")

    def ensure_valid_amount(self) -> None:
        if self.amount < 0:
            raise InvoiceError("Invoice amount cannot be negative")
