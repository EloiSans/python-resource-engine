from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Self

from resource_engine import ResourceRegistry
from resource_engine.errors import InboxNotSupported
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_engine_sqlalchemy.inbox import SqlAlchemyInbox
from resource_engine_sqlalchemy.outbox import SqlAlchemyOutbox
from resource_engine_sqlalchemy.repository import SqlAlchemyRepository

type RepositoryFactory = Callable[[AsyncSession], Any]


class SqlAlchemyUnitOfWork:
    """One ``AsyncSession`` and one transaction per operation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repositories: Mapping[str, RepositoryFactory],
        *,
        outbox_table: Table | None = None,
        inbox_table: Table | None = None,
    ) -> None:
        self._inbox_table = inbox_table
        self._session_factory = session_factory
        self._repositories = repositories
        self._session: AsyncSession | None = None
        self._cache: dict[str, Any] = {}
        self._committed = False
        self._outbox_table = outbox_table

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("Unit of work is not active")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self._cache = {}
        self._committed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            await self.session.close()
            self._session = None

    def repository(self, resource_name: str) -> Any:
        if resource_name not in self._cache:
            try:
                factory = self._repositories[resource_name]
            except KeyError:
                raise KeyError(
                    f"No SQLAlchemy repository configured for resource {resource_name!r}"
                ) from None
            self._cache[resource_name] = factory(self.session)
        return self._cache[resource_name]

    def outbox(self) -> SqlAlchemyOutbox:
        if self._outbox_table is None:
            raise RuntimeError(
                "This unit of work has no outbox; pass outbox_table=outbox_table(metadata)"
            )
        return SqlAlchemyOutbox(self.session, self._outbox_table)

    def inbox(self) -> SqlAlchemyInbox:
        if self._inbox_table is None:
            raise InboxNotSupported(
                "This SQLAlchemy unit of work has no inbox table",
                hint="Pass inbox_table=inbox_table(metadata) to the unit of work factory.",
            )
        return SqlAlchemyInbox(self.session, self._inbox_table)

    async def commit(self) -> None:
        await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self.session.rollback()


def unit_of_work_factory(
    session_factory: async_sessionmaker[AsyncSession],
    registry: ResourceRegistry,
    overrides: Mapping[str, RepositoryFactory] | None = None,
    *,
    outbox_table: Table | None = None,
    inbox_table: Table | None = None,
) -> Callable[[], SqlAlchemyUnitOfWork]:
    """Build a ``uow_factory`` with a generic repository for every registered resource.

    Pass ``overrides`` to use a custom repository for some resources and
    ``outbox_table`` to enable the transactional outbox and ``inbox_table`` for
    idempotent consumers.
    """
    repositories: dict[str, RepositoryFactory] = {}
    for definition in registry:
        model, id_field = definition.model, definition.id_field
        repositories[definition.name] = lambda session, m=model, f=id_field: SqlAlchemyRepository(
            session, m, f
        )
    repositories.update(overrides or {})
    return lambda: SqlAlchemyUnitOfWork(
        session_factory, repositories, outbox_table=outbox_table, inbox_table=inbox_table
    )
