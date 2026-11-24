from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from resource_engine.domain import DomainError


class InvoiceError(DomainError):
    code = "INVOICE"


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
    notes: str | None = None
    status: str | None = None
    rejection_reason: str | None = None
    version: int | None = None
    id: int | None = field(default=None)

    def ensure_editable(self) -> None:
        if self.status not in (None, "draft"):
            raise InvoiceError(f"Invoice {self.id} is {self.status} and can no longer be modified")

    def ensure_valid_amount(self) -> None:
        if self.amount < 0:
            raise InvoiceError("Invoice amount cannot be negative")
