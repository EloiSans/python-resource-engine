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


@dataclass(frozen=True)
class FieldAccess:
    """Field-level outcome for one record: ``denied`` fields are hidden (read) or
    protected (write); ``conditional`` ones depend on data not known yet (the
    record or the input). Callers treat conditional as denied once both are known.
    """

    denied: frozenset[str] = frozenset()
    conditional: frozenset[str] = frozenset()

    @property
    def blocked(self) -> frozenset[str]:
        return self.denied | self.conditional


class FieldRules(Protocol):
    """Field decisions for one principal, resource and mode, prepared once and
    evaluated per record (cheap, synchronous)."""

    def decide(self, *, resource: Any = ..., input: Any = ...) -> FieldAccess: ...


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

    # Optional: authorizers without field rules leave every field open.
    # async def field_rules(self, principal, resource: str, mode: str) -> FieldRules


@dataclass(frozen=True)
class AuthzRequest:
    """One question to an external policy engine.

    ``resource`` and ``input`` are plain dicts (Python values: ``Decimal``,
    ``date``... kept; adapters encode them for their engine), or ``...`` when
    not known yet. ``resource_type`` is the resource name (``"invoices"``);
    ``id_field`` names its identifier.

    ``filter`` is true when a residual will be applied to records (a ``list``
    row filter, field rules); otherwise a residual only has to say "depends on
    the record" (step 2), so engines may answer without enumerating records.
    """

    subject: dict[str, Any] | None
    action: str
    resource_type: str | None
    resource: Any = ...
    input: Any = ...
    context: dict[str, Any] = field(default_factory=dict)
    entity_id: Any = None
    id_field: str = "id"
    filter: bool = False

    @property
    def unknowns(self) -> tuple[str, ...]:
        """Namespaces not known yet (``resource``, ``input``)."""
        return tuple(n for n in ("resource", "input") if getattr(self, n) is ...)


class PolicyEngine(Protocol):
    """An external policy engine (OPA, Cedar, OpenFGA...) behind
    ``EngineAuthorizer``."""

    async def evaluate(self, request: AuthzRequest) -> Any:
        """``True``/``False`` when decided; otherwise a residual condition (the
        engine-neutral condition language of ``resource_engine.authz.conditions``)
        over the unknown namespaces. Raise ``ConditionNotSupported`` when the
        engine's residual cannot be expressed."""
        ...
