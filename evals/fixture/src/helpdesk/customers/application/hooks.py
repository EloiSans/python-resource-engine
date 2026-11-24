from __future__ import annotations

from resource_engine import OperationContext


def validate(ctx: OperationContext) -> None:
    """Checks the customer's business rules before it is saved."""
    ctx.entity.ensure_valid()
