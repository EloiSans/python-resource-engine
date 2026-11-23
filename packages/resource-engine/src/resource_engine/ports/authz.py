"""Authorization port: may this principal perform this action (on this record)?"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from resource_engine.ports.identity import Principal


@dataclass(frozen=True)
class Decision:
    """Outcome of an authorization check.

    ``allowed`` is ``None`` when it cannot be decided yet (the record is not
    loaded and some policy depends on it).
    """

    allowed: bool | None
    reason: str
    policies: tuple[str, ...] = field(default_factory=tuple)
    system_role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policies": list(self.policies),
            "system_role": self.system_role,
        }


class Authorizer(Protocol):
    async def check(
        self,
        principal: Principal | None,
        action: str,
        *,
        resource: Any = ...,
        input: Any = ...,
        context: dict[str, Any] | None = None,
    ) -> Decision:
        """``resource``/``input`` left as ``...`` are unknown (partial check)."""
        ...

    async def scope(self, principal: Principal | None, action: str) -> Any:
        """Row filter for ``action`` (a ``list``): ``True`` (all rows), ``False``
        (none) or a condition over ``resource.*``."""
        ...
