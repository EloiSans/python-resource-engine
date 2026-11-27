from __future__ import annotations

from typing import Any, Protocol

from resource_engine.application.query import ListQuery, Page


class Repository[E](Protocol):
    """Persistence port for the entities of one resource.

    Implementations must make ``add``/``update``/``delete`` visible inside the
    current unit of work (e.g. flush), and assign the identifier on ``add``.

    Optional capabilities, declared as class attributes:

    - ``supports_conditions``: ``list`` applies ``ListQuery.condition``
      (row-level permissions).
    - ``supports_versions``: ``update`` and ``delete`` accept
      ``expected_version`` and raise ``VersionConflict`` when the stored version
      differs at write time (atomically: compare and set). Needed by resources
      with a ``version_field``.
    - ``filterable`` / ``sortable``: the fields ``list`` can filter and sort
      by (``None``: any), and ``max_offset``: the deepest offset it pages to.
      The engine refuses other queries with ``QueryNotSupported`` (RE-E103).

    Units of work declare ``transaction_scope`` (``"unit_of_work"``,
    ``"partition"`` or ``"none"``) and ``max_writes``. See
    ``resource_engine.capabilities``.
    """

    async def get(self, entity_id: Any) -> E | None: ...

    async def list(self, query: ListQuery) -> Page[E]: ...

    async def add(self, entity: E) -> E: ...

    async def update(self, entity: E) -> E: ...

    async def delete(self, entity: E) -> None: ...
