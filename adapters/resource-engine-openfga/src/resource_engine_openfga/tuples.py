"""``TupleSync``: keep relationship tuples in step with records."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from openfga_sdk.client.models import ClientTuple

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext

    from resource_engine_openfga.engine import OpenFgaEngine

type Relations = Callable[[Any], Iterable[tuple[str, str]]]


class TupleSync:
    """Writes the tuples a record implies (``(user, relation)`` pairs on the
    record's object) when it is created, and deletes them when it is deleted.

    Creation writes before the commit and undoes the write if the operation
    rolls back (a staged effect); deletion removes tuples after the commit.
    Records changed in other ways (owner reassigned...) need their own hook.

    Example::

        tuples = TupleSync(engine, {"invoices": lambda i: [(engine.user_ref(i.owner), "owner")]})

        class InvoiceResource(Resource):
            hooks = tuples.hooks("invoices")
    """

    def __init__(self, engine: OpenFgaEngine, relations: Mapping[str, Relations]) -> None:
        self.engine = engine
        self.relations = dict(relations)

    def tuples(self, resource: str, entity: Any, entity_id: Any) -> list[ClientTuple]:
        target = self.engine.object(resource, entity_id)
        return [
            ClientTuple(user=user, relation=relation, object=target)
            for user, relation in self.relations[resource](entity)
        ]

    async def write(self, resource: str, entity: Any, entity_id: Any) -> None:
        tuples = self.tuples(resource, entity, entity_id)
        if tuples:
            await self.engine.client.write_tuples(tuples)

    async def delete(self, resource: str, entity: Any, entity_id: Any) -> None:
        tuples = self.tuples(resource, entity, entity_id)
        if tuples:
            await self.engine.client.delete_tuples(tuples)

    def hooks(self, resource: str) -> dict[str, Any]:
        """Hooks for ``Resource.hooks`` (merge with your own)."""

        async def created(ctx: OperationContext) -> None:
            entity = ctx.entity
            entity_id = getattr(entity, ctx.resource.id_field)
            await self.write(resource, entity, entity_id)
            ctx.on_rollback(lambda: self.delete(resource, entity, entity_id))

        async def deleted(ctx: OperationContext) -> None:
            entity, entity_id = ctx.entity, ctx.entity_id
            ctx.on_commit(lambda: self.delete(resource, entity, entity_id))

        return {"after_create": created, "after_delete": deleted}
