from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from resource_engine.domain.events import DomainEvent

if TYPE_CHECKING:
    from resource_engine.application.resource import ResourceDefinition
    from resource_engine.ports.repository import Repository
    from resource_engine.ports.unit_of_work import UnitOfWork


type Callback = Callable[[], Awaitable[None] | None]


@dataclass(kw_only=True)
class OperationContext:
    """Everything a hook, state-machine bridge or action can see about an operation.

    Attributes:
        resource: Definition of the resource being operated on.
        operation: ``list``, ``get``, ``create``, ``update``, ``delete``,
            ``transition`` or ``action``.
        transition: Event name when ``operation == "transition"``.
        action: Action name when ``operation == "action"``.
        slot: Document slot for ``attach`` / ``detach`` operations.
        actor: Opaque identity of the caller, provided by the entry adapter.
            Authorization is not part of the engine yet.
        raw_input: Input as received, before validation (mutable in ``before_validate``).
        data: Validated input (a Pydantic model instance), or ``None``.
        entity_id: Identifier of the target entity, when there is one.
        entity: The loaded or newly built entity.
        previous_state / new_state: State values around a transition.
        uow: Active unit of work (``None`` outside the transaction).
        events: Events recorded so far; published after commit.
        result: Value returned to the caller (set before commit).
        correlation_id: Identifier shared by logs and events of this operation.
        extras: Free-form storage for hooks to share data.
        documents: Attach/detach documents inside this transaction (``None`` when
            the manager has no blob store).
        decision: The last authorization ``Decision`` for this operation.
    """

    resource: ResourceDefinition
    operation: str
    transition: str | None = None
    action: str | None = None
    slot: str | None = None
    actor: Any = None
    raw_input: Any = None
    data: Any = None
    entity_id: Any = None
    entity: Any = None
    previous_state: Any = None
    new_state: Any = None
    uow: UnitOfWork | None = None
    events: list[DomainEvent] = field(default_factory=list)
    result: Any = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    services: Mapping[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    documents: Any = None
    decision: Any = None
    _on_rollback: list[Callback] = field(default_factory=list, repr=False)
    _on_commit: list[Callback] = field(default_factory=list, repr=False)

    def on_rollback(self, callback: Callback) -> None:
        """Register a compensation for a *staged effect*.

        Use it for writes outside the database that nothing references until the
        commit (a stored blob, a remote record with a delete call). If the
        operation fails, callbacks run in reverse order, best effort: errors are
        logged. A crash can skip them, so pair them with a cleanup job.
        """
        self._on_rollback.append(callback)

    def on_commit(self, callback: Callback) -> None:
        """Run ``callback`` after a successful commit, before ``after_commit`` hooks.

        For deferred clean-ups (e.g. deleting a detached blob). Errors are logged.
        """
        self._on_commit.append(callback)

    def record_event(self, event: DomainEvent) -> None:
        """Queue an event; it is published only if the transaction commits."""
        self.events.append(event)

    def repository(self, resource_name: str | None = None) -> Repository[Any]:
        """Repository of this (or another) resource inside the current transaction."""
        if self.uow is None:
            raise RuntimeError("No unit of work is active at this point of the lifecycle")
        return self.uow.repository(resource_name or self.resource.name)

    def service(self, name: str) -> Any:
        """Look up a service registered on the ResourceManager (e.g. a mail gateway port)."""
        try:
            return self.services[name]
        except KeyError:
            raise KeyError(
                f"Service {name!r} is not registered; pass it to ResourceManager(services=...)"
            ) from None
