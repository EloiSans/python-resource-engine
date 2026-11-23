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
    """

    async def get(self, entity_id: Any) -> E | None: ...

    async def list(self, query: ListQuery) -> Page[E]: ...

    async def add(self, entity: E) -> E: ...

    async def update(self, entity: E) -> E: ...

    async def delete(self, entity: E) -> None: ...
