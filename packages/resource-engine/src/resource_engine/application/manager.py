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
from resource_engine.application.transaction import Transaction
from resource_engine.application.transaction import current as current_transaction
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
    TenantMismatch,
    TenantRequired,
    TransactionDoomed,
    UnknownTransition,
    ValidationFailed,
    VersionConflict,
    VersioningNotSupported,
    VersionRequired,
)
from resource_engine.messaging.delivery import AfterCommitDelivery, EventDelivery
from resource_engine.ports.audit import AuditSink
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
        audit: AuditSink | bool | None = None,
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
        if audit is True:
            from resource_engine.audit.sink import RepositoryAuditSink

            audit = RepositoryAuditSink(uow_factory)
        self.audit: AuditSink | None = audit or None
        target = getattr(self.audit, "resource", None)
        if target is not None and target not in {d.name for d in registry}:
            raise ConfigurationError(
                f"The audit sink writes to {target!r}, which is not registered",
                hint="Call registry.register_audit() (and map its table with SQLAlchemy).",
                doc="docs/audit.md#setup",
            )
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
        self,
        definition: ResourceDefinition,
        operation: str,
        *,
        tenant: str | None = None,
        expected_version: int | None = None,
        **kw: Any,
    ) -> OperationContext:
        ctx = OperationContext(
            resource=definition, operation=operation, services=self.services, **kw
        )
        ctx.extras["requested_tenant"] = tenant
        ctx.extras["expected_version"] = expected_version
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
        await self._check_tenant(ctx, entity)
        ctx.entity = entity
        return entity

    # ------------------------------------------------------------------ tenancy

    async def _ensure_tenant(self, ctx: OperationContext) -> None:
        """Resolve the caller's tenant scope once per operation (tenant-scoped
        resources only): RE-E708 without a tenant, RE-E709 for another one."""
        if ctx.resource.tenant_field is None or ctx._tenant_scope is not None:
            return
        from resource_engine.tenancy import resolve_tenant

        try:
            scope = await resolve_tenant(
                ctx.actor, self.authorizer, ctx.extras.get("requested_tenant")
            )
        except (TenantRequired, TenantMismatch) as error:
            await self._audit_denied(ctx, ctx.operation, error)
            raise
        ctx._tenant_scope = scope
        ctx.tenant = scope.tenant

    async def _check_tenant(self, ctx: OperationContext, entity: Any) -> None:
        """Records of other tenants do not exist for the caller (404)."""
        field_name = ctx.resource.tenant_field
        if field_name is None:
            return
        await self._ensure_tenant(ctx)
        if ctx.tenant is not None and getattr(entity, field_name, None) != ctx.tenant:
            await self._audit_denied(
                ctx,
                ctx.operation,
                PermissionDenied("other tenant", details={"reason": "other tenant"}),
            )
            raise EntityNotFound(
                f"{ctx.resource.name} {ctx.entity_id!r} does not exist",
                details={"resource": ctx.resource.name, "id": ctx.entity_id},
            )

    async def _stamp_tenant(self, ctx: OperationContext, entity: Any) -> None:
        """Create: the new record belongs to the scope's tenant."""
        try:
            self._stamp(ctx, entity)
        except (TenantRequired, TenantMismatch) as error:
            await self._audit_denied(ctx, "create", error)
            raise

    def _stamp(self, ctx: OperationContext, entity: Any) -> None:
        field_name = ctx.resource.tenant_field
        if field_name is None:
            return
        sent = getattr(entity, field_name, None)
        scope = ctx._tenant_scope
        target = ctx.tenant or sent
        if sent is not None and target is not None and sent != target:
            raise TenantMismatch(
                f"Not allowed to create {ctx.resource.name} for tenant {sent!r}",
                details={"reason": "other tenant", "tenant": sent},
                doc="docs/tenancy.md",
            )
        if target is None:
            raise TenantRequired(
                f"Creating {ctx.resource.name} needs a tenant",
                hint=f"Cross-tenant callers pass tenant=... (or set {field_name!r}).",
                details={"reason": "no tenant", "cross_tenant": bool(scope and scope.cross)},
                doc="docs/tenancy.md",
            )
        setattr(entity, field_name, target)
        ctx.tenant = target

    def _tenant_query(self, ctx: OperationContext, query: ListQuery) -> ListQuery | None:
        """``list``: only the scope's tenant (``None`` when the client asked for
        another one)."""
        field_name = ctx.resource.tenant_field
        if field_name is None or ctx.tenant is None:
            return query
        asked = query.filters.get(field_name)
        if asked is not None and asked != ctx.tenant:
            return None
        return dataclasses.replace(query, filters={**query.filters, field_name: ctx.tenant})

    def _id_of(self, ctx: OperationContext, entity: Any) -> Any:
        return getattr(entity, ctx.resource.id_field, None)

    # ------------------------------------------------------------- authorization

    async def _gate(
        self, ctx: OperationContext, action: str, *, resource: Any = ..., input: Any = ...
    ) -> None:
        """Step 2 (``resource`` unknown) or 5b (record known): raise when denied.
        Tenant-scoped resources resolve the caller's tenant first."""
        await self._ensure_tenant(ctx)
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
            error = PermissionDenied(
                f"Not allowed: {ctx.resource.name}:{action}",
                hint="Ask an administrator for a role or policy that grants it.",
                details={"action": f"{ctx.resource.name}:{action}", "reason": decision.reason},
            )
            await self._audit_denied(ctx, action, error)
            raise error

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
                error = EntityNotFound(
                    f"{ctx.resource.name} {ctx.entity_id!r} does not exist",
                    details={"resource": ctx.resource.name, "id": ctx.entity_id},
                )
                await self._audit_denied(
                    ctx,
                    action,
                    PermissionDenied("record not visible", details={"reason": "not visible"}),
                )
                raise error
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
        self,
        actor: Any,
        resource: ResourceRef,
        action: str,
        entity_id: Any = None,
        *,
        tenant: str | None = None,
    ) -> bool:
        """Whether ``actor`` may perform ``action`` (``"update"``,
        ``"transition:approve"``...), on ``entity_id`` if given. Without an
        authorizer only tenant scoping applies."""
        definition = self.registry.get(resource)
        name = f"{definition.name}:{action}"
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id, tenant=tenant)
        try:
            await self._ensure_tenant(ctx)
        except (TenantRequired, TenantMismatch):
            return False
        if entity_id is None:
            if self.authorizer is None:
                return True
            return (await self.authorizer.check(actor, name)).allowed is not False
        async with self._reading(ctx) as uow:
            entity = await uow.repository(definition.name).get(entity_id)
            if entity is None:
                return False
            field_name = definition.tenant_field
            if (
                field_name
                and ctx.tenant is not None
                and getattr(entity, field_name, None) != ctx.tenant
            ):
                return False
            if self.authorizer is None:
                return True
            return (await self.authorizer.check(actor, name, resource=entity)).allowed is True

    # -------------------------------------------------------------- versions

    def _versioned_repository(self, ctx: OperationContext) -> Any:
        repository = ctx.repository()
        if not getattr(repository, "supports_versions", False):
            raise VersioningNotSupported(
                f"The repository of {ctx.resource.name!r} cannot check versions",
                hint="Use a repository with supports_versions (in-memory, SQLAlchemy), "
                "or remove version_field: remote APIs own their concurrency.",
                doc="docs/concurrency.md",
            )
        return repository

    async def _check_version(self, ctx: OperationContext, entity: Any) -> None:
        """Steps 5/5b: the client's version (``expected_version=`` or the version
        field in the input) must be the loaded one."""
        version_field = ctx.resource.version_field
        if version_field is None:
            return
        self._versioned_repository(ctx)
        expected = ctx.extras.get("expected_version")
        data = ctx.data
        if isinstance(data, BaseModel) and version_field in data.model_fields_set:
            sent = getattr(data, version_field)
            data.model_fields_set.discard(version_field)  # not an input value
            if expected is None:
                expected = sent
        current = getattr(entity, version_field, None)
        ctx.extras["loaded_version"] = current
        action = ctx.operation if ctx.operation in ("update", "delete") else _action_of(ctx)
        if expected is None:
            if ctx.resource.require_version:
                error = VersionRequired(
                    f"{ctx.resource.name} {ctx.entity_id!r}: send the version you read",
                    hint="Send If-Match with the ETag of the record "
                    "(or expected_version=), see docs/concurrency.md.",
                    details={"resource": ctx.resource.name, "id": ctx.entity_id},
                )
                await self._audit_denied(ctx, action, error)
                raise error
            return
        if expected != current:
            error = _conflict(ctx, expected, current)
            await self._audit_denied(ctx, action, error)
            raise error

    async def _write(self, ctx: OperationContext, entity: Any) -> Any:
        """``Repository.update``, compare-and-set on versioned resources."""
        if ctx.resource.version_field is None:
            return await ctx.repository().update(entity)
        repository = self._versioned_repository(ctx)
        loaded = ctx.extras.get("loaded_version")
        try:
            return await repository.update(entity, expected_version=loaded)
        except VersionConflict as error:
            await self._audit_denied(ctx, _action_of(ctx), error)
            raise

    async def _remove(self, ctx: OperationContext, entity: Any) -> None:
        if ctx.resource.version_field is None:
            await ctx.repository().delete(entity)
            return
        repository = self._versioned_repository(ctx)
        try:
            await repository.delete(entity, expected_version=ctx.extras.get("loaded_version"))
        except VersionConflict as error:
            await self._audit_denied(ctx, "delete", error)
            raise

    # -------------------------------------------------------------------- audit

    def _audited(self, ctx: OperationContext) -> bool:
        return self.audit is not None and getattr(ctx.resource.resource_cls, "audit", True)

    async def _actor_roles(self, actor: Any) -> list[str]:
        from resource_engine.authz.evaluator import SystemPrincipal
        from resource_engine.ports.identity import Principal

        if isinstance(actor, SystemPrincipal) or not isinstance(actor, Principal):
            return []
        roles_of = getattr(self.authorizer, "roles_of", None)
        try:
            return sorted(await roles_of(actor)) if roles_of else sorted(actor.roles)
        except Exception:  # pragma: no cover - roles are informative here
            logger.exception("Could not resolve roles of %s for the audit trail", actor.id)
            return sorted(actor.roles)

    async def _before_commit(self, ctx: OperationContext, uow: UnitOfWork) -> None:
        """Step 9, before commit: the audit entry (same transaction), then events."""
        if self._audited(ctx):
            from resource_engine.audit.recorder import entry_for

            entry = entry_for(ctx, roles=await self._actor_roles(ctx.actor))
            await self.audit.record([entry], uow=uow)  # type: ignore[union-attr]
        await self.delivery.before_commit(ctx, uow)

    async def _audit_alone(self, ctx: OperationContext, **kw: Any) -> None:
        """Entries outside the operation's transaction (denials, reads): best
        effort, never changes the operation's outcome."""
        if not self._audited(ctx):
            return
        from resource_engine.audit.recorder import entry_for

        try:
            entry = entry_for(ctx, roles=await self._actor_roles(ctx.actor), **kw)
            await self.audit.record([entry])  # type: ignore[union-attr]
        except Exception:
            logger.exception(
                "Writing the audit entry failed for %s (correlation_id=%s)",
                ctx.resource.name,
                ctx.correlation_id,
            )

    async def _audit_denied(self, ctx: OperationContext, action: str, error: Exception) -> None:
        await self._audit_alone(
            ctx,
            outcome="denied",
            action=action,
            details={
                "code": getattr(error, "code", type(error).__name__),
                "reason": (getattr(error, "details", None) or {}).get("reason") or str(error),
                **(
                    {"fields": error.details["fields"]}
                    if isinstance(getattr(error, "details", None), dict)
                    and "fields" in error.details
                    else {}
                ),
                **(
                    {
                        "expected": error.details.get("expected"),
                        "current": error.details.get("current"),
                    }
                    if isinstance(error, VersionConflict)
                    else {}
                ),
            },
        )

    async def _audit_read(self, ctx: OperationContext, details: dict[str, Any]) -> None:
        if getattr(ctx.resource.resource_cls, "audit_reads", False):
            await self._audit_alone(ctx, details=details)

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
            error = FieldNotWritable(
                f"Not allowed to write {', '.join(blocked)} of {ctx.resource.name}",
                hint="GET /authz/me lists the fields you may write.",
                details={"resource": ctx.resource.name, "fields": blocked},
            )
            await self._audit_denied(ctx, ctx.operation, error)
            raise error

    async def _gate_query(self, ctx: OperationContext, query: ListQuery, rules: Any) -> None:
        """Filtering or sorting by hidden fields would reveal them (RE-E707)."""
        if rules is None:
            return
        used = set(query.filters) | {o.lstrip("-") for o in query.order_by}
        blocked = sorted(used & rules.decide().blocked)
        if blocked:
            error = FieldNotReadable(
                f"Not allowed to filter or sort {ctx.resource.name} by {', '.join(blocked)}",
                details={"resource": ctx.resource.name, "fields": blocked},
            )
            await self._audit_denied(ctx, "list", error)
            raise error

    def _joined(self) -> Transaction | None:
        tx = current_transaction.get()
        return tx if tx is not None and tx.owner is self and not tx.finished else None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """Run several operations in one unit of work (see
        ``resource_engine.application.transaction``). Nested calls join the
        outer transaction."""
        joined = self._joined()
        if joined is not None:
            yield joined
            return
        async with self.uow_factory() as uow:
            tx = Transaction(self, uow)
            token = current_transaction.set(tx)
            try:
                yield tx
                if tx.doomed is not None:
                    cause = tx.doomed
                    raise TransactionDoomed(
                        "An operation failed inside the transaction; it can only roll back",
                        hint="Let the error propagate, or check before calling the manager.",
                        details={
                            "code": getattr(cause, "code", type(cause).__name__),
                            "message": str(cause),
                        },
                    ) from cause
                await uow.commit()
            except BaseException:
                for ctx in reversed(tx.contexts):
                    await _run_callbacks(reversed(ctx._on_rollback), "rollback", ctx)
                raise
            finally:
                tx.finished = True
                current_transaction.reset(token)
        for ctx, qualifier in tx.after_commit:
            await self._after_commit(ctx, qualifier)

    @asynccontextmanager
    async def _reading(self, ctx: OperationContext | None = None) -> AsyncIterator[Any]:
        """A unit of work for reads: the transaction's, if one is open (reads then
        see its uncommitted writes)."""
        tx = self._joined()
        if tx is not None:
            if ctx is not None:
                ctx.uow = tx.view
            yield tx.view
            return
        async with self.uow_factory() as uow:
            if ctx is not None:
                ctx.uow = uow
            yield uow

    @asynccontextmanager
    async def _transaction(self, ctx: OperationContext) -> AsyncIterator[UnitOfWork]:
        """Steps 4-9: open the unit of work (or join ``transaction()``); on
        failure run staged-effect compensations (or doom the transaction)."""
        tx = self._joined()
        if tx is not None:
            ctx.uow = tx.view
            ctx.extras["transaction"] = tx
            tx.contexts.append(ctx)
            try:
                yield ctx.uow
            except BaseException as exc:
                if tx.doomed is None:
                    tx.doomed = exc
                raise
            return
        try:
            async with self.uow_factory() as uow:
                ctx.uow = uow
                yield uow
        except BaseException:
            await _run_callbacks(reversed(ctx._on_rollback), "rollback", ctx)
            raise

    async def _after_commit(self, ctx: OperationContext, qualifier: str | None = None) -> None:
        tx = ctx.extras.get("transaction")
        if tx is not None and not tx.finished:  # runs when the transaction commits
            tx.after_commit.append((ctx, qualifier))
            return
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
        self,
        resource: ResourceRef,
        query: ListQuery | None = None,
        *,
        actor: Any = None,
        tenant: str | None = None,
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
        ctx = self._context(definition, "list", actor=actor, raw_input=query, tenant=tenant)
        await self._gate(ctx, "list")
        tenant_query = self._tenant_query(ctx, query)
        if tenant_query is None:
            return Page([], 0, query.offset, query.limit)
        query = tenant_query
        rules = await self._read_rules(ctx)
        await self._gate_query(ctx, query, rules)
        async with self._reading(ctx):
            scoped = await self._scoped(ctx, query)
            if scoped is None:
                return Page([], 0, query.offset, query.limit)
            page = await ctx.repository().list(scoped)
            result = page.map(lambda e: self._to_read(ctx, e, rules))
        await self._audit_read(
            ctx,
            {
                "filters": {k: str(v) for k, v in query.filters.items()},
                "order_by": list(query.order_by),
                "offset": query.offset,
                "returned": len(result.items),
                "total": result.total,
            },
        )
        return result

    async def get(
        self, resource: ResourceRef, entity_id: Any, *, actor: Any = None, tenant: str | None = None
    ) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "get")
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id, tenant=tenant)
        await self._gate(ctx, "get")
        async with self._reading(ctx):
            entity = await self._load(ctx)
            await self._gate_record(ctx, "get", entity)
            result = self._to_read(ctx, entity, await self._read_rules(ctx))
        await self._audit_read(ctx, {})
        return result

    # -------------------------------------------------------------------- write

    async def create(
        self, resource: ResourceRef, data: Any, *, actor: Any = None, tenant: str | None = None
    ) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "create")
        ctx = self._context(definition, "create", actor=actor, raw_input=data, tenant=tenant)
        await self._gate(ctx, "create")
        await self._validate(ctx, definition.schemas["create"])
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = definition.resource_cls.build_entity(ctx.data, ctx)
            ctx.entity = entity
            if definition.machine:
                await definition.machine.initialize(entity)
            if definition.version_field and getattr(entity, definition.version_field, None) is None:
                setattr(entity, definition.version_field, 1)
            await self._stamp_tenant(ctx, entity)
            await self._gate(ctx, "create", resource=entity, input=ctx.data)
            await self._gate_fields(ctx, entity, ctx.data)
            await hooks.run(HookPoint.BEFORE_CREATE, ctx)
            entity = await ctx.repository().add(ctx.entity)
            ctx.entity = entity
            ctx.entity_id = self._id_of(ctx, entity)
            ctx.record_event(ResourceCreated(resource=definition.name, entity_id=ctx.entity_id))
            await hooks.run(HookPoint.AFTER_CREATE, ctx)
            ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            await self._before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)
        return ctx.result

    async def update(
        self,
        resource: ResourceRef,
        entity_id: Any,
        data: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expected_version: int | None = None,
    ) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "update")
        ctx = self._context(
            definition,
            "update",
            actor=actor,
            raw_input=data,
            entity_id=entity_id,
            tenant=tenant,
            expected_version=expected_version,
        )
        await self._gate(ctx, "update")
        await self._validate(ctx, definition.schemas["update"])
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, "update", entity, input=ctx.data)
            await self._gate_fields(ctx, entity, ctx.data)
            await self._check_version(ctx, entity)
            await hooks.run(HookPoint.BEFORE_UPDATE, ctx)
            sent = ctx.data.model_dump(exclude_unset=True)
            before = {k: getattr(entity, k, None) for k in sent}
            definition.resource_cls.apply_update(entity, ctx.data, ctx)
            changes = {
                k: (old, getattr(entity, k, None))
                for k, old in before.items()
                if getattr(entity, k, None) != old
            }
            ctx.entity = await self._write(ctx, entity)
            ctx.record_event(
                ResourceUpdated(resource=definition.name, entity_id=entity_id, changes=changes)
            )
            await hooks.run(HookPoint.AFTER_UPDATE, ctx)
            ctx.result = self._to_read(ctx, ctx.entity, await self._read_rules(ctx))
            await self._before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)
        return ctx.result

    async def delete(
        self,
        resource: ResourceRef,
        entity_id: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        definition = self.registry.get(resource)
        self._require(definition, "delete")
        ctx = self._context(
            definition,
            "delete",
            actor=actor,
            entity_id=entity_id,
            tenant=tenant,
            expected_version=expected_version,
        )
        await self._gate(ctx, "delete")
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, "delete", entity)
            await self._check_version(ctx, entity)
            await hooks.run(HookPoint.BEFORE_DELETE, ctx)
            await self._remove(ctx, entity)
            if self.documents is not None and definition.document_slots:
                await self.documents.cascade(ctx)
            ctx.record_event(ResourceDeleted(resource=definition.name, entity_id=entity_id))
            await hooks.run(HookPoint.AFTER_DELETE, ctx)
            await self._before_commit(ctx, uow)
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
        tenant: str | None = None,
        expected_version: int | None = None,
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
            tenant=tenant,
            expected_version=expected_version,
        )
        await self._gate(ctx, f"transition:{event}")
        await self._validate(ctx, definition.transition_schemas.get(event), event)
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, f"transition:{event}", entity, input=ctx.data)
            await self._check_version(ctx, entity)
            ctx.previous_state = machine.current(entity)
            if (
                self.documents is not None
                and definition.document_slots
                and event in machine.allowed_events(entity)
            ):
                await self.documents.check_required(ctx, event)
            await hooks.run(HookPoint.BEFORE_TRANSITION, ctx, event)
            payload = (
                ctx.data.model_dump(exclude={definition.version_field} - {None})
                if ctx.data is not None
                else {}
            )
            previous, new = await machine.fire(entity, event, payload)
            ctx.previous_state, ctx.new_state = previous, new
            ctx.entity = await self._write(ctx, entity)
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
            await self._before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx, event)
        return ctx.result

    async def allowed_transitions(
        self, resource: ResourceRef, entity_id: Any, *, actor: Any = None, tenant: str | None = None
    ) -> list[str]:
        definition = self.registry.get(resource)
        if definition.machine is None:
            return []
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id, tenant=tenant)
        async with self._reading(ctx):
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
        tenant: str | None = None,
        expected_version: int | None = None,
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
            definition,
            "action",
            action=action,
            actor=actor,
            raw_input=data,
            entity_id=entity_id,
            tenant=tenant,
            expected_version=expected_version,
        )
        await self._gate(ctx, f"action:{action}")
        await self._validate(ctx, action_def.input_schema, action)
        hooks = definition.dispatcher
        async with self._transaction(ctx) as uow:
            if action_def.instance:
                entity = await self._load(ctx)
                await self._gate_record(ctx, f"action:{action}", entity, input=ctx.data)
                if definition.version_field:  # claim the version before the action writes
                    await self._check_version(ctx, entity)
                    ctx.entity = await self._write(ctx, entity)
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
            await self._before_commit(ctx, uow)
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


def _action_of(ctx: OperationContext) -> str:
    if ctx.operation == "transition":
        return f"transition:{ctx.transition}"
    if ctx.operation == "action":
        return f"action:{ctx.action}"
    return ctx.operation


def _conflict(ctx: OperationContext, expected: Any, current: Any) -> VersionConflict:
    return VersionConflict(
        f"{ctx.resource.name} {ctx.entity_id!r} changed: version {current}, "
        f"the write was based on {expected}",
        hint="Reload the record and apply your change again.",
        details={
            "resource": ctx.resource.name,
            "id": ctx.entity_id,
            "expected": expected,
            "current": current,
            "reason": "version conflict",
        },
        doc="docs/concurrency.md",
    )
