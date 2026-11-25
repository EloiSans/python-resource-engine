"""``ResourceManager.transaction()``: several operations in one unit of work.

Operations called inside ``async with manager.transaction():`` join the shared
unit of work instead of committing their own. Their audit entries and outbox
events are written in it; their post-commit work (``ctx.on_commit``,
``after_commit`` hooks, direct event delivery) runs only once the transaction
commits. If an operation fails after reaching its unit of work, the transaction
is *doomed*: it can only roll back (RE-E007), because the failed operation may
have written part of its changes.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext
    from resource_engine.ports.unit_of_work import UnitOfWork


@dataclass(eq=False)
class Transaction:
    """The shared unit of work of ``manager.transaction()``.

    Attributes:
        uow: The unit of work (repositories, ``outbox()``, ``inbox()``).
        doomed: The first failure of a joined operation, if any.
    """

    owner: Any
    uow: UnitOfWork
    doomed: BaseException | None = None
    contexts: list[OperationContext] = field(default_factory=list)
    after_commit: list[tuple[OperationContext, str | None]] = field(default_factory=list)
    finished: bool = False

    @property
    def view(self) -> JoinedUnitOfWork:
        return joined_view(self.uow)


class JoinedUnitOfWork:
    """What a joined operation sees: the shared unit of work, without commit."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def __aenter__(self) -> JoinedUnitOfWork:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        """Deferred to the transaction."""

    async def rollback(self) -> None:
        """Deferred to the transaction (which is doomed by the failure)."""

    def repository(self, resource_name: str) -> Any:
        return self._uow.repository(resource_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._uow, name)


#: Optional capabilities a joined view forwards when the shared unit of work has them.
CAPABILITIES = ("outbox", "inbox", "idempotency")

_views: dict[frozenset[str], type[JoinedUnitOfWork]] = {}


def _forward(name: str) -> Any:
    def method(self: JoinedUnitOfWork) -> Any:
        return getattr(self._uow, name)()

    method.__name__ = name
    return method


def joined_view(uow: UnitOfWork) -> JoinedUnitOfWork:
    """A view with the same capabilities (``outbox()``, ``inbox()``,
    ``idempotency()``) as ``uow``: runtime protocol checks look at the class,
    not at ``__getattr__``, so the methods are defined on it."""
    present = frozenset(name for name in CAPABILITIES if hasattr(uow, name))
    cls = _views.get(present)
    if cls is None:
        name = "Joined" + "".join(n.capitalize() for n in sorted(present)) + "UnitOfWork"
        cls = type(name, (JoinedUnitOfWork,), {n: _forward(n) for n in present})
        _views[present] = cls
    return cls(uow)


current: ContextVar[Transaction | None] = ContextVar("resource_engine_transaction", default=None)
