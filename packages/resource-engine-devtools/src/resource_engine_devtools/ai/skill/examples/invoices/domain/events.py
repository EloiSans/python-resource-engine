from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from resource_engine.domain import DomainEvent


@dataclass(frozen=True, kw_only=True)
class InvoiceTotalsRecalculated(DomainEvent):
    """L'import d'una factura s'ha recalculat aplicant un descompte."""

    invoice_id: int
    previous_amount: Decimal
    new_amount: Decimal


@dataclass(frozen=True, kw_only=True)
class PaymentOverdue(DomainEvent):
    """Una factura aprovada fa 15 dies que espera el cobrament."""

    invoice_id: int
