"""One operation across several stores: SQL resources and remote ones (REST,
SOAP, OData) or query-engine views, with compensations for remote writes.

```python
from resource_engine.composite import CompositeStore

store = CompositeStore(
    sql_uow,                                        # the primary store: outbox, inbox...
    {"partners": erp_uow, "daily_sales": lake.unit_of_work},
    compensations={"erp_orders": {"add": cancel_order}},
)
manager = ResourceManager(registry, store.unit_of_work)
```

- **Routing:** each resource goes to the store that holds it; everything
  else, and the outbox, inbox and idempotency records, to the primary store.
  Resources sharing a factory share one unit of work per operation.
- **Remote writes** happen when they are made: a remote system can't take
  part in the database transaction. Each one records its compensation, the
  write that undoes it (a create is undone by a delete, an update by the
  record as it was, a delete by creating it again). If the operation fails,
  or the primary commit fails after them, the compensations run in reverse
  order.
- **Compensations are best effort:** a failing one is logged and reported
  (``ErrorReporter``) and the others still run. When "delete" isn't the right
  inverse for a remote API (an order must be cancelled, not deleted), give
  your own per resource and write kind.
- Transactional stores (``transactional`` not ``False``) commit and roll back
  normally; read-only ones (query engines) just serve reads.
"""

from __future__ import annotations

import copy
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from resource_engine.application.query import ListQuery, Page

log = logging.getLogger("resource_engine.composite")

type Compensation = Callable[..., Awaitable[None] | None]


@dataclass
class _Undo:
    resource: str
    kind: str
    repository: Any
    before: Any
    after: Any


class CompensatingRepository:
    """Wraps a non-transactional repository: every write records how to undo it."""

    def __init__(
        self,
        inner: Any,
        resource: str,
        log_: list[_Undo],
        *,
        id_field: str = "id",
    ) -> None:
        self.inner = inner
        self.resource = resource
        self.undo_log = log_
        self.id_field = id_field
        self.supports_conditions = getattr(inner, "supports_conditions", False)
        self.supports_versions = getattr(inner, "supports_versions", False)

    async def get(self, entity_id: Any) -> Any:
        return await self.inner.get(entity_id)

    async def list(self, query: ListQuery) -> Page[Any]:
        return await self.inner.list(query)

    async def add(self, entity: Any) -> Any:
        stored = await self.inner.add(entity)
        self.undo_log.append(_Undo(self.resource, "add", self.inner, None, copy.copy(stored)))
        return stored

    async def update(self, entity: Any, **kw: Any) -> Any:
        before = await self.inner.get(getattr(entity, self.id_field))
        stored = await self.inner.update(entity, **kw)
        self.undo_log.append(
            _Undo(self.resource, "update", self.inner, copy.copy(before), copy.copy(stored))
        )
        return stored

    async def delete(self, entity: Any, **kw: Any) -> None:
        before = copy.copy(entity)
        await self.inner.delete(entity, **kw)
        self.undo_log.append(_Undo(self.resource, "delete", self.inner, before, None))


class CompositeUnitOfWork:
    """Routes repositories to their stores and coordinates commit and rollback."""

    def __init__(
        self,
        primary: Callable[[], Any],
        stores: Mapping[str, Callable[[], Any]],
        compensations: Mapping[str, Mapping[str, Compensation]],
        id_fields: Mapping[str, str],
    ) -> None:
        self._primary_factory = primary
        self._stores = stores
        self._compensations = compensations
        self._id_fields = id_fields
        self.primary: Any = None
        self._opened: dict[int, Any] = {}  # by factory id: one unit of work per store
        self._wrapped: dict[str, Any] = {}
        self._undo: list[_Undo] = []
        self._committed = False

    async def __aenter__(self) -> CompositeUnitOfWork:
        self.primary = self._primary_factory()
        await self.primary.__aenter__()
        return self

    async def _open(self, factory: Callable[[], Any]) -> Any:
        key = id(factory)
        if key not in self._opened:
            uow = factory()
            await uow.__aenter__()
            self._opened[key] = uow
        return self._opened[key]

    def repository(self, resource_name: str) -> Any:
        if resource_name not in self._stores:
            return self.primary.repository(resource_name)
        if resource_name in self._wrapped:
            return self._wrapped[resource_name]
        raise RuntimeError(  # pragma: no cover - opened by ``prepare``
            f"The store of {resource_name!r} isn't open"
        )

    async def prepare(self, resource_name: str) -> None:
        """Open the store of ``resource_name`` (the manager calls ``repository``
        synchronously, so stores are opened up front by ``unit_of_work``)."""
        factory = self._stores[resource_name]
        uow = await self._open(factory)
        inner = uow.repository(resource_name)
        if getattr(uow, "transactional", True) is False:
            inner = CompensatingRepository(
                inner, resource_name, self._undo, id_field=self._id_fields.get(resource_name, "id")
            )
        self._wrapped[resource_name] = inner

    def __getattr__(self, name: str) -> Any:
        # outbox(), inbox(), idempotency()...: the primary store's
        if name.startswith("_") or self.primary is None:
            raise AttributeError(name)
        return getattr(self.primary, name)

    async def commit(self) -> None:
        try:
            for uow in self._opened.values():
                if getattr(uow, "transactional", True) is not False:
                    await uow.commit()
            await self.primary.commit()
        except BaseException:
            await self._compensate()
            raise
        self._committed = True
        self._undo.clear()

    async def rollback(self) -> None:
        await self.primary.rollback()
        for uow in self._opened.values():
            await uow.rollback()
        await self._compensate()

    async def _compensate(self) -> None:
        while self._undo:
            undo = self._undo.pop()
            custom = self._compensations.get(undo.resource, {}).get(undo.kind)
            try:
                if custom is not None:
                    result = custom(undo.repository, undo.before, undo.after)
                    if inspect.isawaitable(result):
                        await result
                elif undo.kind == "add":
                    await undo.repository.delete(undo.after)
                elif undo.kind == "update" and undo.before is not None:
                    await undo.repository.update(undo.before)
                elif undo.kind == "delete":
                    await undo.repository.add(undo.before)
            except Exception as exc:
                log.exception("Compensation of %s %s failed", undo.kind, undo.resource)
                from resource_engine.reporting import report_error

                await report_error(
                    exc,
                    where="composite.compensation",
                    force=True,
                    resource=undo.resource,
                    write=undo.kind,
                )

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if not self._committed:
                await self.rollback()
        finally:
            for uow in self._opened.values():
                await uow.__aexit__(exc_type, exc, tb)
            await self.primary.__aexit__(exc_type, exc, tb)


class _Opening:
    """Async context manager that opens the composite and its stores."""

    def __init__(self, uow: CompositeUnitOfWork, resources: list[str]) -> None:
        self.uow = uow
        self.resources = resources

    async def __aenter__(self) -> CompositeUnitOfWork:
        await self.uow.__aenter__()
        try:
            for name in self.resources:
                await self.uow.prepare(name)
        except BaseException as exc:
            await self.uow.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        return self.uow

    async def __aexit__(self, *exc: Any) -> None:
        await self.uow.__aexit__(*exc)


class CompositeStore:
    """Units of work spanning a primary store and others, by resource.

    Args:
        primary: The primary store's unit-of-work factory (SQL): resources not
            listed in ``stores``, and the outbox, inbox and idempotency records.
        stores: Resource name → the unit-of-work factory that holds it.
        compensations: Resource name → ``{"add"|"update"|"delete": fn}``, each
            ``fn(repository, before, after)`` undoing that write instead of
            the default inverse.
        registry: To read each resource's id field (default ``id``).
    """

    def __init__(
        self,
        primary: Callable[[], Any],
        stores: Mapping[str, Callable[[], Any]],
        *,
        compensations: Mapping[str, Mapping[str, Compensation]] | None = None,
        registry: Any = None,
    ) -> None:
        self.primary = primary
        self.stores = dict(stores)
        self.compensations = dict(compensations or {})
        self.id_fields = (
            {name: registry.get(name).id_field for name in self.stores if name in registry}
            if registry is not None
            else {}
        )

    def unit_of_work(self) -> _Opening:
        uow = CompositeUnitOfWork(self.primary, self.stores, self.compensations, self.id_fields)
        return _Opening(uow, list(self.stores))


__all__ = ["CompensatingRepository", "CompositeStore", "CompositeUnitOfWork"]
