from __future__ import annotations

from dataclasses import dataclass

from resource_engine.domain import DomainError


class CustomerError(DomainError):
    code = "CUSTOMERS"


@dataclass
class Customer:
    """A customer of the help desk.

    Business Rules:
        - A customer has a non-empty name.
    """

    name: str
    email: str | None = None
    id: int | None = None

    def ensure_valid(self) -> None:
        """Refuse customers without a name."""
        if not self.name.strip():
            raise CustomerError("A customer needs a name")
