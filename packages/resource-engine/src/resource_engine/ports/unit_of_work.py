from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, Self

from resource_engine.ports.repository import Repository


class UnitOfWork(Protocol):
    """A transaction boundary that hands out repositories.

    Leaving the ``async with`` block without calling ``commit`` must roll back.
    """

    def repository(self, resource_name: str) -> Repository[Any]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
