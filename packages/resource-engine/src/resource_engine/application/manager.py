"""The ResourceManager executes operations following the lifecycle contract.

The contract (see docs/lifecycle.md) for a writing operation is:

1. resolve resource and operation
2. authorization without the record (``Authorizer.check``; no-op without an authorizer)
3. ``before_validate`` → validation → ``after_validate``
4. open the unit of work
5. load the entity (update / delete / transition / instance action), then
   authorization on it (404 when invisible); ``list`` gets the row filter
6. ``before_<op>``
7. mutate: build+add / apply+update / delete / fire transition / execute action
8. ``after_<op>`` (still transactional)
9. build the result, stage events (``EventDelivery.before_commit``: outbox), commit
10. ``after_commit`` hooks, then ``EventDelivery.after_commit`` (direct publishing)

Any exception before step 9 rolls the transaction back. Failures after commit are
logged and never undo a committed operation.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from resource_engine.application.context import OperationContext
from resource_engine.application.events import (
    ActionExecuted,
    ResourceCreated,
    ResourceDeleted,
    ResourceUpdated,
    TransitionExecuted,
)
from resource_engine.application.hooks import HookPoint
from resource_engine.application.query import ListQuery, Page
from resource_engine.application.registry import ResourceRegistry
from resource_engine.application.resource import Resource, ResourceDefinition
from resource_engine.errors import (
    ActionNotFound,
    ConditionNotSupported,
    ConfigurationError,
    EntityNotFound,
    FieldNotReadable,
    FieldNotWritable,
    InvalidQuery,
    OperationNotEnabled,
    PermissionDenied,
    UnknownTransition,
    ValidationFailed,
)
from resource_engine.messaging.delivery import AfterCommitDelivery, EventDelivery
from resource_engine.ports.authz import Authorizer
from resource_engine.ports.event_publisher import EventPublisher, NullEventPublisher
from resource_engine.ports.storage import BlobStore
from resource_engine.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

if TYPE_CHECKING:
    from resource_engine.documents.manager import DocumentManager

logger = logging.getLogger("resource_engine")

type ResourceRef = str | type[Resource]


class ResourceManager:
    """Runs CRUD operations, transitions and actions of registered resources.

    It knows nothing about HTTP or SQL: entry adapters call it, and persistence is
    reached through the ``UnitOfWork`` port.
    """

    def __init__(
        self,
        registry: ResourceRegistry,
        uow_factory: UnitOfWorkFactory,
        *,
        event_publisher: EventPublisher | None = None,
        event_delivery: EventDelivery | None = None,
        services: Mapping[str, Any] | None = None,
        blob_store: BlobStore | None = None,
        documents_resource: str = "documents",
        authorizer: Authorizer | None = None,
    ) -> None:
        self.registry = registry
        self.uow_factory = uow_factory
        if event_publisher is not None and event_delivery is not None:
            raise ValueError("Pass either event_publisher or event_delivery, not both")
        self.delivery: EventDelivery = event_delivery or AfterCommitDelivery(
            event_publisher or NullEventPublisher()
        )
        self.services = dict(services or {})
        self.authorizer = authorizer
        if authorizer is not None:
            bind = getattr(authorizer, "bind", None)
            if bind is not None:
                bind(registry)
            self.services.setdefault("authz", authorizer)
        self.documents: DocumentManager | None = None
        if blob_store is not None:
            from resource_engine.documents.manager import DocumentManager

            self.documents = DocumentManager(self, blob_store, resource=documents_resource)
        else:
            with_slots = [d.name for d in registry if d.document_slots]
            if with_slots:
                raise ConfigurationError(
                    f"Resources {with_slots} declare document slots but no blob store is set",
                    hint="Pass blob_store=... (S3BlobStore, InMemoryBlobStore) and call "
                    "registry.register_documents().",
                    doc="docs/documents.md#setup",
                )

    # ------------------------------------------------------------------ helpers

    def _context(
        self, definition: ResourceDefinition, operation: str, **kw: Any
    ) -> OperationContext:
        ctx = OperationContext(
            resource=definition, operation=operation, services=self.services, **kw
        )
        if self.documents is not None:
            from resource_engine.documents.manager import ContextDocuments

            ctx.documents = ContextDocuments(self.documents, ctx)
        return ctx

    def _require(self, definition: ResourceDefinition, operation: str) -> None:
        if not definition.enabled(operation):
            raise OperationNotEnabled(
                f"Operation {operation!r} is not enabled for resource {definition.name!r}",
                hint=f"Enabled operations: {', '.join(definition.operations) or '(none)'}. "
                "Add it to `operations` on the resource if it should exist.",
            )

    async def _validate(
        self, ctx: OperationContext, schema: type[BaseModel] | None, qualifier: str | None = None
    ) -> None:
        hooks = ctx.resource.dispatcher
        await hooks.run(HookPoint.BEFORE_VALIDATE, ctx, None)
        if schema is None:
            ctx.data = None
        elif isinstance(ctx.raw_input, schema):
            ctx.data = ctx.raw_input
        else:
            raw = ctx.raw_input
            if isinstance(raw, BaseModel):
                raw = raw.model_dump(exclude_unset=True)
            try:
                ctx.data = schema.model_validate(raw if raw is not None else {})
            except ValidationError as exc:
                raise ValidationFailed(
                    f"Invalid input for {ctx.resource.name}.{qualifier or ctx.operation}",
                    hint="Check `details` for the offending fields.",
                    details=exc.errors(include_url=False, include_context=False),
                ) from exc
        await hooks.run(HookPoint.AFTER_VALIDATE, ctx, None)

    async def _load(self, ctx: OperationContext) -> Any:
        entity = await ctx.repository().get(ctx.entity_id)
        if entity is None:
            raise EntityNotFound(
                f"{ctx.resource.name} {ctx.entity_id!r} does not exist",
                details={"resource": ctx.resource.name, "id": ctx.entity_id},
            )
        ctx.entity = entity
        return entity

    def _id_of(self, ctx: OperationContext, entity: Any) -> Any:
        return getattr(entity, ctx.resource.id_field, None)

    # ------------------------------------------------------------- authorization

    async def _gate(
        self, ctx: OperationContext, action: str, *, resource: Any = ..., input: Any = ...
    ) -> None:
        """Step 2 (``resource`` unknown) or 5b (record known): raise when denied."""
        if self.authorizer is None:
            return
        decision = await self.authorizer.check(
            ctx.actor,
            f"{ctx.resource.name}:{action}",
            resource=resource,
            input=input,
            context={"operation": ctx.operation},
        )
        ctx.decision = decision
        if decision.allowed is False or (decision.allowed is None and resource is not ...):
            logger.info(
                "denied %s:%s to %s: %s",
                ctx.resource.name,
                action,
                getattr(ctx.actor, "id", None),
                decision.reason,
            )
            raise PermissionDenied(
                f"Not allowed: {ctx.resource.name}:{action}",
                hint="Ask an administrator for a role or policy that grants it.",
                details={"action": f"{ctx.resource.name}:{action}", "reason": decision.reason},
            )

    async def _gate_record(
        self, ctx: OperationContext, action: str, entity: Any, *, input: Any = ...
    ) -> None:
        """Step 5b: the record must be visible (else 404) and the action allowed."""
        if self.authorizer is None:
            return
        if ctx.resource.enabled("get"):
            visible = await self.authorizer.check(
                ctx.actor, f"{ctx.resource.name}:get", resource=entity
            )
            if visible.allowed is not True:
                raise EntityNotFound(
                    f"{ctx.resource.name} {ctx.entity_id!r} does not exist",
                    details={"resource": ctx.resource.name, "id": ctx.entity_id},
                )
        if action != "get":
            await self._gate(ctx, action, resource=entity, input=input)

    async def _scoped(self, ctx: OperationContext, query: ListQuery) -> ListQuery | None:
        """Row-level filter for ``list``; ``None`` when nothing is visible."""
        if self.authorizer is None:
            return query
        scope = await self.authorizer.scope(ctx.actor, f"{ctx.resource.name}:list")
        if scope is True:
            return query
        if scope is False:
            return None
        from resource_engine.authz.conditions import conjunction

        if not getattr(ctx.repository(), "supports_conditions", False):
            raise ConditionNotSupported(
                f"The repository of {ctx.resource.name!r} cannot filter rows by policy",
                hint="Use a repository with supports_conditions (in-memory, SQLAlchemy) "
                "or grant list without conditions.",
            )
        return dataclasses.replace(query, condition=conjunction(query.condition, scope))

    async def can(
        self, actor: Any, resource: ResourceRef, action: str, entity_id: Any = None
    ) -> bool:
        """Whether ``actor`` may perform ``action`` (``"update"``,
        ``"transition:approve"``...), on ``entity_id`` if given. Always ``True``
        without an authorizer."""
        if self.authorizer is None:
            return True
        definition = self.registry.get(resource)
        name = f"{definition.name}:{action}"
        if entity_id is None:
            return (await self.authorizer.check(actor, name)).allowed is not False
        async with self.uow_factory() as uow:
            entity = await uow.repository(definition.name).get(entity_id)
            if entity is None:
                return False
            return (await self.authorizer.check(actor, name, resource=entity)).allowed is True

    async def _field_rules(self, ctx: OperationContext, mode: str) -> Any:
        """Prepared field rules (``None`` when no field can be blocked)."""
        prepare = getattr(self.authorizer, "field_rules", None)
        if prepare is None:
            return None
        rules = await prepare(ctx.actor, ctx.resource.name, mode)
        return None if getattr(rules, "empty", False) else rules

    def _to_read(self, ctx: OperationContext, entity: Any, rules: Any = None) -> Any:
        """Step 9 result: the read schema, without the fields the caller may not
        read (omitted)."""
        result = ctx.resource.resource_cls.to_read(entity, ctx)
        if rules is None:
            return result
        hidden = rules.decide(resource=entity).blocked
        if hidden:
            from resource_engine.authz.fields import mask

            ctx.extras["hidden_fields"] = hidden
            return mask(result, hidden)
        return result

    async def _read_rules(self, ctx: OperationContext) -> Any:
        return await self._field_rules(ctx, "read") if self.authorizer is not None else None

    async def _gate_fields(self, ctx: OperationContext, entity: Any, data: Any) -> None:
        """Step 5b for writes: every field sent must be writable (RE-E706)."""
        if self.authorizer is None or data is None:
            return
        rules = await self._field_rules(ctx, "write")
        if rules is None:
            return
        sent = set(data.model_dump(exclude_unset=True)) if isinstance(data, BaseModel) else set()
        blocked = sorted(sent & rules.decide(resource=entity, input=data).blocked)
        if blocked:
            raise FieldNotWritable(
                f"Not allowed to write {', '.join(blocked)} of {ctx.resource.name}",
                hint="GET /authz/me lists the fields you may write.",
                details={"resource": ctx.resource.name, "fields": blocked},
            )

    async def _gate_query(self, ctx: OperationContext, query: ListQuery, rules: Any) -> None:
        """Filtering or sorting by hidden fields would reveal them (RE-E707)."""
        if rules is None:
            return
        used = set(query.filters) | {o.lstrip("-") for o in query.order_by}
        blocked = sorted(used & rules.decide().blocked)
        if blocked:
            raise FieldNotReadable(
                f"Not allowed to filter or sort {ctx.resource.name} by {', '.join(blocked)}",
                details={"resource": ctx.resource.name, "fields": blocked},
            )

    @asynccontextmanager
    async def _transaction(self, ctx: OperationContext) -> AsyncIterator[UnitOfWork]:
        """Steps 4-9: open the unit of work; on failure run staged-effect compensations."""
        try:
            async with self.uow_factory() as uow:
                ctx.uow = uow
                yield uow
        except BaseException:
            await _run_callbacks(reversed(ctx._on_rollback), "rollback", ctx)
            raise

    async def _after_commit(self, ctx: OperationContext, qualifier: str | None = None) -> None:
        ctx.uow = None
        await _run_callbacks(ctx._on_commit, "on_commit", ctx)
        for hook in ctx.resource.dispatcher.for_point(HookPoint.AFTER_COMMIT, None):
            try:
                result = hook(ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception(
                    "after_commit hook %r failed for %s (correlation_id=%s)",
                    getattr(hook, "__qualname__", hook),
                    ctx.resource.name,
                    ctx.correlation_id,
                )
                ctx.extras.setdefault("after_commit_errors", []).append(hook)
        try:
            await self.delivery.after_commit(ctx)
        except Exception:
            logger.exception(
                "Delivering %d events failed for %s (correlation_id=%s)",
                len(ctx.events),
                ctx.resource.name,
                ctx.correlation_id,
            )

    # --------------------------------------------------------------------- read

    async def list(
        self, resource: ResourceRef, query: ListQuery | None = None, *, actor: Any = None
    ) -> Page[Any]:
        definition = self.registry.get(resource)
        self._require(definition, "list")
        query = query or ListQuery()
        bad_filters = [f for f in query.filters if f not in definition.filterable]
        bad_order = [
            o.lstrip("-") for o in query.order_by if o.lstrip("-") not in definition.sortable
        ]
        if bad_filters or bad_order:
            raise InvalidQuery(
                f"Invalid query for {definition.name}: "
                + "; ".join(
                    part
                    for part in (
                        bad_filters and f"not filterable: {bad_filters}",
                        bad_order and f"not sortable: {bad_order}",
                    )
                    if part
                ),
                hint=(
                    f"Filterable: {list(definition.filterable)}; "
                    f"sortable: {list(definition.sortable)}"
                ),
            )
        if query.limit < 1 or query.limit > definition.max_page_size or query.offset < 0:
            raise InvalidQuery(
                f"limit must be between 1 and {definition.max_page_size} and offset >= 0",
            )
        ctx = self._context(definition, "list", actor=actor, raw_input=query)
        await self._gate(ctx, "list")
        rules = await self._read_rules(ctx)
        await self._gate_query(ctx, query, rules)
        async with self.uow_factory() as uow:
            ctx.uow = uow
            scoped = await self._scoped(ctx, query)
            if scoped is None:
                return Page([], 0, query.offset, query.limit)
            page = await ctx.repository().list(scoped)
            return page.map(lambda e: self._to_read(ctx, e, rules))

    async def get(self, resource: ResourceRef, entity_id: Any, *, actor: Any = None) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "get")
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id)
        await self._gate(ctx, "get")
        async with self.uow_factory() as uow:
            ctx.uow = uow
            entity = await self._load(ctx)
            await self._gate_record(ctx, "get", entity)
            return self._to_read(ctx, entity, await self._read_rules(ctx))

    # -------------------------------------------------------------------- write

    async def create(self, resource: ResourceRef, data: Any, *, actor: Any = None) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "create")
        ctx = self._context(definition, "create", actor=actor, raw_input=data)
        await self._gate(ctx, "create")
        await self._validate(ctx, definition.schemas["create"])
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = definition.resource_cls.build_entity(ctx.data, ctx)
            ctx.entity = entity
            if definition.machine:
                await definition.machine.initialize(entity)
            await self._gate(ctx, "create", resource=entity, input=ctx.data)
            await self._gate_fields(ctx, entity, ctx.data)
            await hooks.run(HookPoint.BEFORE_CREATE, ctx)
            entity = await ctx.repository().add(ctx.entity)
            ctx.entity = entity
            ctx.entity_id = self._id_of(ctx, entity)
            ctx.record_event(ResourceCreated(resource=definition.name, entity_id=ctx.entity_id))
            await hooks.run(HookPoint.AFTER_CREATE, ctx)
            ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            await self.delivery.before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)
        return ctx.result

    async def update(
        self, resource: ResourceRef, entity_id: Any, data: Any, *, actor: Any = None
    ) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "update")
        ctx = self._context(definition, "update", actor=actor, raw_input=data, entity_id=entity_id)
        await self._gate(ctx, "update")
        await self._validate(ctx, definition.schemas["update"])
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, "update", entity, input=ctx.data)
            await self._gate_fields(ctx, entity, ctx.data)
            await hooks.run(HookPoint.BEFORE_UPDATE, ctx)
            sent = ctx.data.model_dump(exclude_unset=True)
            before = {k: getattr(entity, k, None) for k in sent}
            definition.resource_cls.apply_update(entity, ctx.data, ctx)
            changes = {
                k: (old, getattr(entity, k, None))
                for k, old in before.items()
                if getattr(entity, k, None) != old
            }
            ctx.entity = await ctx.repository().update(entity)
            ctx.record_event(
                ResourceUpdated(resource=definition.name, entity_id=entity_id, changes=changes)
            )
            await hooks.run(HookPoint.AFTER_UPDATE, ctx)
            ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            await self.delivery.before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)
        return ctx.result

    async def delete(self, resource: ResourceRef, entity_id: Any, *, actor: Any = None) -> None:
        definition = self.registry.get(resource)
        self._require(definition, "delete")
        ctx = self._context(definition, "delete", actor=actor, entity_id=entity_id)
        await self._gate(ctx, "delete")
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, "delete", entity)
            await hooks.run(HookPoint.BEFORE_DELETE, ctx)
            await ctx.repository().delete(entity)
            if self.documents is not None and definition.document_slots:
                await self.documents.cascade(ctx)
            ctx.record_event(ResourceDeleted(resource=definition.name, entity_id=entity_id))
            await hooks.run(HookPoint.AFTER_DELETE, ctx)
            await self.delivery.before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)

    async def transition(
        self,
        resource: ResourceRef,
        entity_id: Any,
        event: str,
        data: Any = None,
        *,
        actor: Any = None,
    ) -> Any:
        definition = self.registry.get(resource)
        machine = definition.machine
        if machine is None:
            raise UnknownTransition(
                f"Resource {definition.name!r} has no state machine",
                hint="Declare `state_machine = <StateMachine subclass>` on the resource.",
            )
        if event not in machine.info.events:
            raise UnknownTransition(
                f"Resource {definition.name!r} has no transition {event!r}",
                hint=f"Available transitions: {', '.join(machine.info.events)}",
            )
        ctx = self._context(
            definition,
            "transition",
            transition=event,
            actor=actor,
            raw_input=data,
            entity_id=entity_id,
        )
        await self._gate(ctx, f"transition:{event}")
        await self._validate(ctx, definition.transition_schemas.get(event), event)
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, f"transition:{event}", entity, input=ctx.data)
            ctx.previous_state = machine.current(entity)
            if (
                self.documents is not None
                and definition.document_slots
                and event in machine.allowed_events(entity)
            ):
                await self.documents.check_required(ctx, event)
            await hooks.run(HookPoint.BEFORE_TRANSITION, ctx, event)
            payload = ctx.data.model_dump() if ctx.data is not None else {}
            previous, new = await machine.fire(entity, event, payload)
            ctx.previous_state, ctx.new_state = previous, new
            ctx.entity = await ctx.repository().update(entity)
            ctx.record_event(
                TransitionExecuted(
                    resource=definition.name,
                    entity_id=entity_id,
                    transition=event,
                    source=previous,
                    target=new,
                )
            )
            await hooks.run(HookPoint.AFTER_TRANSITION, ctx, event)
            ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            await self.delivery.before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx, event)
        return ctx.result

    async def allowed_transitions(
        self, resource: ResourceRef, entity_id: Any, *, actor: Any = None
    ) -> list[str]:
        definition = self.registry.get(resource)
        if definition.machine is None:
            return []
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id)
        async with self.uow_factory() as uow:
            ctx.uow = uow
            entity = await self._load(ctx)
            await self._gate_record(ctx, "get", entity)
            events = definition.machine.allowed_events(entity)
            if self.authorizer is None:
                return events
            allowed = []
            for event in events:
                decision = await self.authorizer.check(
                    actor, f"{definition.name}:transition:{event}", resource=entity
                )
                if decision.allowed is not False:  # may still depend on the input
                    allowed.append(event)
            return allowed

    async def run_action(
        self,
        resource: ResourceRef,
        action: str,
        entity_id: Any = None,
        data: Any = None,
        *,
        actor: Any = None,
    ) -> Any:
        definition = self.registry.get(resource)
        action_def = definition.actions.get(action)
        if action_def is None:
            raise ActionNotFound(
                f"Resource {definition.name!r} has no action {action!r}",
                hint=f"Available actions: {', '.join(definition.actions) or '(none)'}",
            )
        if action_def.instance and entity_id is None:
            raise ValidationFailed(f"Action {action!r} requires an entity id")
        ctx = self._context(
            definition, "action", action=action, actor=actor, raw_input=data, entity_id=entity_id
        )
        await self._gate(ctx, f"action:{action}")
        await self._validate(ctx, action_def.input_schema, action)
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            if action_def.instance:
                entity = await self._load(ctx)
                await self._gate_record(ctx, f"action:{action}", entity, input=ctx.data)
            else:
                await self._gate(ctx, f"action:{action}", resource=None, input=ctx.data)
            await hooks.run(HookPoint.BEFORE_ACTION, ctx, action)
            result = action_def.action_cls().execute(ctx)
            if inspect.isawaitable(result):
                result = await result
            ctx.record_event(
                ActionExecuted(resource=definition.name, entity_id=entity_id, action=action)
            )
            await hooks.run(HookPoint.AFTER_ACTION, ctx, action)
            if action_def.output_schema is not None:
                ctx.result = (
                    result
                    if isinstance(result, action_def.output_schema)
                    else action_def.output_schema.model_validate(result, from_attributes=True)
                )
            elif result is None and action_def.instance and "read" in definition.schemas:
                ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            else:
                ctx.result = result
            await self.delivery.before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx, action)
        return ctx.result


async def _run_callbacks(callbacks: Any, kind: str, ctx: OperationContext) -> None:
    for callback in list(callbacks):
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "%s callback %r failed for %s (correlation_id=%s)",
                kind,
                getattr(callback, "__qualname__", callback),
                ctx.resource.name,
                ctx.correlation_id,
            )
