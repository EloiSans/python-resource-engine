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

import contextlib
import dataclasses
import inspect
import logging
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
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
from resource_engine.application.idempotency import (
    DEFAULT_TTL,
    idempotent,
    pending_for,
    store_result,
)
from resource_engine.application.query import ListQuery, Page
from resource_engine.application.registry import ResourceRegistry
from resource_engine.application.relations import (
    ReferenceDefinition,
    related_filter_names,
    split_filters,
)
from resource_engine.application.resource import Resource, ResourceDefinition
from resource_engine.application.transaction import Transaction
from resource_engine.application.transaction import current as current_transaction
from resource_engine.audit.history import StateChange
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
    ReferenceInUse,
    ReferenceNotFound,
    TenantMismatch,
    TenantRequired,
    TransactionDoomed,
    UnknownTransition,
    ValidationFailed,
    VersionConflict,
    VersioningNotSupported,
    VersionRequired,
)
from resource_engine.logs import log_context, update_log_context
from resource_engine.messaging.delivery import AfterCommitDelivery, EventDelivery
from resource_engine.ports.audit import AuditSink
from resource_engine.ports.authz import Authorizer
from resource_engine.ports.event_publisher import EventPublisher, NullEventPublisher
from resource_engine.ports.observability import OperationObserver
from resource_engine.ports.storage import BlobStore
from resource_engine.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from resource_engine.reporting import report_error
from resource_engine.tracing import get_tracer, traced

if TYPE_CHECKING:
    from resource_engine.documents.manager import DocumentManager

#: Related records a filter like ``customer.country`` may match (docs/relationships.md).
MAX_RELATED_MATCHES = 1000
#: Records an inline reverse expansion embeds by default (``expand=["invoices"]``).
EXPAND_PAGE_SIZE = 10
#: Levels of a nested expansion (``invoices.customer_record`` is two).
MAX_EXPAND_DEPTH = 3

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
        audit_failures: bool = False,
        idempotency_ttl: timedelta = DEFAULT_TTL,
        observers: Sequence[OperationObserver] = (),
        rate_limiter: Any = None,
        cache: Any = None,
        generator: Any = None,
    ) -> None:
        self.registry = registry
        from resource_engine.generation import check_printouts

        check_printouts(registry, generator)
        #: The ``DocumentGenerator`` behind ``Resource.printouts`` (docs/documents.md).
        self.generator = generator
        from resource_engine.cache import RecordCache

        #: Records read by id, for resources with ``cache_ttl`` (docs/caching.md).
        self.records = RecordCache(cache) if cache is not None else None
        #: ``RateLimiter`` whose ``actions`` limits apply to every operation (RE-E710).
        self.rate_limiter = rate_limiter
        self.observers: list[OperationObserver] = list(observers)
        self.uow_factory = uow_factory
        if event_publisher is not None and event_delivery is not None:
            raise ValueError("Pass either event_publisher or event_delivery, not both")
        self.delivery: EventDelivery = event_delivery or AfterCommitDelivery(
            event_publisher or NullEventPublisher()
        )
        self.services = dict(services or {})
        self.idempotency_ttl = idempotency_ttl
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
        #: Also audit operations that failed inside their transaction
        #: (``outcome="failed"``, with the error code), not only refusals.
        self.audit_failures = audit_failures
        target = getattr(self.audit, "resource", None)
        if target is not None and target not in {d.name for d in registry}:
            raise ConfigurationError(
                f"The audit sink writes to {target!r}, which is not registered",
                hint="Call registry.register_audit() (and map its table with SQLAlchemy).",
                doc="docs/audit.md#setup",
            )
        from resource_engine.application.relations import check_references, reverse_relations
        from resource_engine.application.views import check_views

        check_views(registry)
        #: References pointing to each resource, by target name (docs/relationships.md).
        self.dependents = check_references(registry)
        #: Reverse relations of each resource, by target and related name.
        self.reverse = reverse_relations(registry, self.dependents)
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
        from resource_engine.exports import ExportJobs

        #: Background exports (docs/exports.md), when a blob store can hold the files.
        self.exports: ExportJobs | None = (
            ExportJobs(self, blob_store) if blob_store is not None else None
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
        pending_for(ctx)  # bind an idempotency key to this operation, if one was sent
        context = log_context()
        # the first context of the running operation (traced() opened its log context)
        if context.get("operation") is not None and context.get("correlation_id") is None:
            update_log_context(
                correlation_id=ctx.correlation_id,
                entity_id=None if ctx.entity_id is None else str(ctx.entity_id),
                action=_action_of(ctx) if ctx.transition or ctx.action else None,
            )
        span = get_tracer().current()
        span.set_attribute("resource_engine.correlation_id", ctx.correlation_id)
        if ctx.entity_id is not None:
            span.set_attribute("resource_engine.entity_id", str(ctx.entity_id))
        if ctx.transition or ctx.action:
            span.set_attribute("resource_engine.action", _action_of(ctx))
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
        cached = (
            self.records is not None
            and ctx.resource.cache_ttl is not None
            and ctx.operation == "get"  # reads only; writes load the stored record
            and self._joined() is None
        )
        entity = await self.records.load(ctx.resource, ctx.entity_id) if cached else None
        if entity is None:
            entity = await ctx.repository().get(ctx.entity_id)
            if cached and entity is not None:
                await self.records.store(ctx.resource, entity)
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
        if resource is ... and self.rate_limiter is not None:
            await self.rate_limiter.action(ctx.actor, f"{ctx.resource.name}:{action}")
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
        """Step 9, before commit: the idempotency record and the audit entry (same
        transaction), then events."""
        await store_result(self, ctx, uow)
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

    async def _audit_failed(self, ctx: OperationContext, error: BaseException) -> None:
        """An operation failed after it started writing (``audit_failures``)."""
        if not self.audit_failures or ctx.extras.get("audited_denial"):
            return
        if not isinstance(error, Exception):
            return  # cancellation, interpreter exit
        from resource_engine.logs import error_type

        await self._audit_alone(
            ctx,
            outcome="failed",
            details={"code": error_type(error), "message": str(error)[:500]},
        )

    async def _audit_denied(self, ctx: OperationContext, action: str, error: Exception) -> None:
        ctx.extras["audited_denial"] = True
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
        subtype = (
            self.registry.subtype_of(ctx.resource, entity) if ctx.resource.discriminator else None
        )
        if subtype is not None:
            # a base record reads as its subtype; the subtype's restricted fields
            # need its own grants, so they stay hidden here
            result = subtype.resource_cls.to_read(entity, ctx)
            if subtype.restricted_fields:
                from resource_engine.authz.fields import mask

                result = mask(result, set(subtype.restricted_fields))
        else:
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
                await self._audit_failed(ctx, exc)
                raise
            return
        try:
            async with self.uow_factory() as uow:
                ctx.uow = uow
                yield uow
        except BaseException as exc:
            await _run_callbacks(reversed(ctx._on_rollback), "rollback", ctx)
            await self._audit_failed(ctx, exc)
            raise

    async def _after_commit(self, ctx: OperationContext, qualifier: str | None = None) -> None:
        tx = ctx.extras.get("transaction")
        if tx is not None and not tx.finished:  # runs when the transaction commits
            tx.after_commit.append((ctx, qualifier))
            return
        ctx.uow = None
        if (
            self.records is not None
            and ctx.resource.cache_ttl is not None
            and ctx.entity_id is not None
            and ctx.operation != "get"
        ):
            try:
                await self.records.forget(ctx.resource, ctx.entity_id)
            except Exception as exc:  # the change is committed: report, don't fail it
                await report_error(exc, where="after_commit", force=True, cache="forget")
        await _run_callbacks(ctx._on_commit, "on_commit", ctx)
        for hook in ctx.resource.dispatcher.for_point(HookPoint.AFTER_COMMIT, None):
            try:
                result = hook(ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                await report_error(
                    exc,
                    where="after_commit",
                    force=True,
                    hook=getattr(hook, "__qualname__", repr(hook)),
                    resource=ctx.resource.name,
                    correlation_id=ctx.correlation_id,
                )
                ctx.extras.setdefault("after_commit_errors", []).append(hook)
        try:
            await self.delivery.after_commit(ctx)
        except Exception as exc:
            await report_error(
                exc,
                where="after_commit",
                force=True,
                events=len(ctx.events),
                resource=ctx.resource.name,
                correlation_id=ctx.correlation_id,
            )

    # --------------------------------------------------------------------- read

    @traced("list")
    async def list(
        self,
        resource: ResourceRef,
        query: ListQuery | None = None,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expand: Sequence[str] = (),
        _within: Mapping[str, Any] | None = None,
        _condition: Any = None,
        _unbounded: bool = False,
    ) -> Page[Any]:
        definition = self.registry.get(resource)
        self._require(definition, "list")
        query = query or ListQuery()
        own, related = split_filters(query.filters)
        bad_filters = [f for f in own if f not in definition.filterable]
        for name, fields in related.items():
            ref = definition.references.get(name)
            target = self.registry.get(ref.target) if ref is not None else None
            bad_filters += [
                f"{name}.{f}" for f in fields if target is None or f not in target.filterable
            ]
        bad_order = [
            o.lstrip("-") for o in query.order_by if o.lstrip("-") not in definition.sortable
        ]
        if bad_filters or bad_order:
            related_names = related_filter_names(definition, self.registry)
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
                    f"Filterable: {[*definition.filterable, *related_names]}; "
                    f"sortable: {list(definition.sortable)}"
                ),
            )
        self._check_expand(definition, expand)
        too_many = query.limit > definition.max_page_size and not _unbounded
        if query.limit < 1 or too_many or query.offset < 0:
            raise InvalidQuery(
                f"limit must be between 1 and {definition.max_page_size} and offset >= 0",
            )
        ctx = self._context(definition, "list", actor=actor, raw_input=query, tenant=tenant)
        await self._gate(ctx, "list")
        # _within: the parent of a reverse relation (not a client filter)
        query = dataclasses.replace(query, filters={**own, **(_within or {})})
        if _condition is not None:  # internal: related records by id
            from resource_engine.authz.conditions import conjunction

            query = dataclasses.replace(query, condition=conjunction(query.condition, _condition))
        tenant_query = self._tenant_query(ctx, query)
        if tenant_query is None:
            return Page([], 0, query.offset, query.limit)
        query = tenant_query
        rules = await self._read_rules(ctx)
        await self._gate_query(ctx, query, rules)
        async with self._reading(ctx):
            if _condition is not None and not getattr(
                ctx.repository(), "supports_conditions", False
            ):
                raise ConditionNotSupported(
                    f"The repository of {definition.name!r} cannot select related records",
                    hint="Many-to-many links need a repository with supports_conditions "
                    "(in-memory, SQLAlchemy).",
                    doc="docs/relationships.md#many-to-many",
                )
            scoped = await self._scoped(ctx, query)
            if scoped is not None and related:
                scoped = await self._related_scope(ctx, scoped, related)
            if scoped is None:
                return Page([], 0, query.offset, query.limit)
            if definition.resource_cls.translatable:
                from resource_engine.translatable import locale_chain

                scoped = dataclasses.replace(
                    scoped,
                    translatable=frozenset(definition.resource_cls.translatable),
                    locales=tuple(locale_chain(definition.resource_cls.default_locale)),
                )
            from resource_engine.capabilities import check_query

            check_query(definition.name, ctx.repository(), scoped)
            page = await ctx.repository().list(scoped)
            result = page.map(lambda e: self._to_read(ctx, e, rules))
            if expand:
                await self._expand(ctx, page.items, result.items, expand)
        await self._audit_read(
            ctx,
            {
                "filters": {
                    k: str(v)
                    for k, v in {
                        **query.filters,
                        **{f"{n}.{f}": v for n, fs in related.items() for f, v in fs.items()},
                    }.items()
                },
                "order_by": list(query.order_by),
                "offset": query.offset,
                "returned": len(result.items),
                "total": result.total,
            },
        )
        return result

    @traced("get")
    async def get(
        self,
        resource: ResourceRef,
        entity_id: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expand: Sequence[str] = (),
    ) -> Any:
        definition = self.registry.get(resource)
        self._require(definition, "get")
        self._check_expand(definition, expand)
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id, tenant=tenant)
        await self._gate(ctx, "get")
        async with self._reading(ctx):
            entity = await self._load(ctx)
            await self._gate_record(ctx, "get", entity)
            result = self._to_read(ctx, entity, await self._read_rules(ctx))
            if expand:
                await self._expand(ctx, [entity], [result], expand)
        await self._audit_read(ctx, {})
        return result

    # --------------------------------------------------------------- references

    def _relation(self, definition: ResourceDefinition, name: str) -> tuple[str, Any, Any] | None:
        """``(kind, relation, definition of the related records)`` for a relation
        name: ``forward`` (a reference), ``reverse`` or ``link`` (many-to-many)."""
        from resource_engine.application.relations import LinkDefinition

        if name in definition.references:
            ref = definition.references[name]
            return "forward", ref, self.registry.get(ref.target)
        rel = self.reverse.get(definition.name, {}).get(name)
        if rel is None:
            return None
        kind = "link" if isinstance(rel, LinkDefinition) else "reverse"
        return kind, rel, self.registry.get(rel.owner)

    def _check_expand(self, definition: ResourceDefinition, expand: Sequence[str]) -> None:
        """Every path names relations (``invoices.customer_record``, at most
        ``MAX_EXPAND_DEPTH`` levels); sizes only on reverse and link relations."""
        from resource_engine.application.relations import parse_expand

        for item in expand:
            path = item.split(".")
            if len(path) > MAX_EXPAND_DEPTH:
                raise InvalidQuery(
                    f"expand {item!r} is nested more than {MAX_EXPAND_DEPTH} levels",
                    doc="docs/relationships.md#nested-expansion",
                )
            current = definition
            for segment in path:
                name, size = parse_expand(segment)
                found = self._relation(current, name)
                if found is None:
                    known = [*current.references, *self.reverse.get(current.name, {})]
                    raise InvalidQuery(
                        f"{current.name} has no relation {name!r} to expand",
                        hint=f"Relations of {current.name}: {known or '(none)'}",
                        doc="docs/relationships.md#expanding",
                    )
                kind, _rel, related = found
                if size is not None:
                    limit = related.max_page_size
                    if kind == "forward" or not 1 <= size <= limit:
                        raise InvalidQuery(
                            f"Invalid expand {item!r}"
                            + (
                                ": only reverse relations take a page size"
                                if kind == "forward"
                                else f": the size must be between 1 and {limit}"
                            ),
                            doc="docs/relationships.md#reverse-relations",
                        )
                current = related

    async def _related_context(
        self, ctx: OperationContext, target: ResourceDefinition
    ) -> OperationContext | None:
        """A read context on ``target`` for the same caller and operation (no
        audit, no idempotency); ``None`` when the caller has no tenant scope there."""
        tctx = OperationContext(
            resource=target,
            operation="get",
            actor=ctx.actor,
            services=self.services,
            correlation_id=ctx.correlation_id,
            uow=ctx.uow,
        )
        tctx.extras["requested_tenant"] = ctx.extras.get("requested_tenant")
        if target.tenant_field is not None:
            from resource_engine.tenancy import resolve_tenant

            try:
                scope = await resolve_tenant(
                    ctx.actor, self.authorizer, ctx.extras.get("requested_tenant") or ctx.tenant
                )
            except (TenantRequired, TenantMismatch):
                return None
            tctx._tenant_scope = scope
            tctx.tenant = scope.tenant
        return tctx

    async def _visible(self, tctx: OperationContext, entity: Any) -> bool:
        field_name = tctx.resource.tenant_field
        if (
            field_name
            and tctx.tenant is not None
            and getattr(entity, field_name, None) != tctx.tenant
        ):
            return False
        if self.authorizer is None:
            return True
        decision = await self.authorizer.check(
            tctx.actor, f"{tctx.resource.name}:get", resource=entity
        )
        return decision.allowed is True

    async def _related(
        self,
        ctx: OperationContext,
        ref: ReferenceDefinition,
        ids: list[Any],
        *,
        read: bool = True,
        nested: Sequence[str] = (),
    ) -> dict[Any, Any]:
        """The records of ``ref.target`` with these ids that the caller may read
        (read models, or entities with ``read=False``), by id."""
        return await self._records(
            ctx, self.registry.get(ref.target), ids, read=read, nested=nested
        )

    async def _records(
        self,
        ctx: OperationContext,
        target: ResourceDefinition,
        ids: list[Any],
        *,
        read: bool = True,
        nested: Sequence[str] = (),
    ) -> dict[Any, Any]:
        tctx = await self._related_context(ctx, target)
        if tctx is None or not ids:
            return {}
        repository = ctx.uow.repository(target.name)  # type: ignore[union-attr]
        if len(ids) > 1 and getattr(repository, "supports_conditions", False):
            condition = {"in": [{"attr": f"resource.{target.id_field}"}, list(ids)]}
            entities = (await repository.list(ListQuery(limit=len(ids), condition=condition))).items
        else:
            entities = [e for i in ids if (e := await repository.get(i)) is not None]
        rules = await self._read_rules(tctx) if read else None
        found: dict[Any, Any] = {}
        visible: list[Any] = []
        for entity in entities:
            if await self._visible(tctx, entity):
                key = getattr(entity, target.id_field)
                found[key] = self._to_read(tctx, entity, rules) if read else entity
                visible.append(entity)
        if read and nested and visible:
            reads = [found[getattr(e, target.id_field)] for e in visible]
            await self._expand(tctx, visible, reads, nested)
        return found

    async def _check_references(
        self, ctx: OperationContext, entity: Any, fields: Collection[str] | None = None
    ) -> None:
        """Writes: each reference set (or changed) points to a record the
        caller can read (RE-E108)."""
        for ref in ctx.resource.references.values():
            if fields is not None and ref.field not in fields:
                continue
            value = getattr(entity, ref.field, None)
            if value is None:
                continue
            found = await self._related(ctx, ref, [value], read=False)
            if value not in found:
                raise ReferenceNotFound(
                    f"{ctx.resource.name}.{ref.field}: there is no {ref.target} record {value!r}",
                    hint=f"Use the id of an existing {ref.target} record you can read.",
                    details={"field": ref.field, "resource": ref.target, "id": value},
                    doc="docs/relationships.md#integrity",
                )

    async def _expand(
        self, ctx: OperationContext, entities: list[Any], results: list[Any], names: Sequence[str]
    ) -> None:
        """Attach the expanded relations to ``results`` (read models of
        ``entities``); nested paths continue on the related records."""
        from resource_engine.application.relations import attach, parse_expand, unique

        groups: dict[str, tuple[int | None, list[str]]] = {}
        for item in names:
            head, _, rest = item.partition(".")
            name, size = parse_expand(head)
            known_size, nested = groups.setdefault(name, (size, []))
            if known_size is None and size is not None:
                groups[name] = (size, nested)
            if rest:
                nested.append(rest)
        for name, (size, nested) in groups.items():
            found = self._relation(ctx.resource, name)
            assert found is not None  # checked by _check_expand
            kind, rel, _related = found
            if kind == "forward":
                ids = unique(getattr(e, rel.field, None) for e in entities)
                records = await self._related(ctx, rel, ids, nested=nested)
                for entity, result in zip(entities, results, strict=True):
                    value = getattr(entity, rel.field, None)
                    attach(result, {name: None if value is None else records.get(value)})
                continue
            parent_ids = [getattr(e, ctx.resource.id_field) for e in entities]
            pages = await self._related_pages(ctx, kind, rel, parent_ids, size, nested)
            for parent_id, result in zip(parent_ids, results, strict=True):
                attach(result, {name: pages.get(parent_id)})

    async def _related_pages(
        self,
        ctx: OperationContext,
        kind: str,
        rel: Any,
        parent_ids: list[Any],
        size: int | None,
        nested: Sequence[str],
    ) -> dict[Any, Any]:
        """One ``RelatedPage`` per parent (``None`` when the caller may not list
        the records). Reverse relations of several parents load in one query
        when they match at most ``MAX_RELATED_MATCHES`` records."""
        from resource_engine.application.relations import RelatedPage

        owner = self.registry.get(rel.owner)
        limit = min(size or EXPAND_PAGE_SIZE, owner.max_page_size)
        needed = [rel.owner] if kind == "reverse" else [rel.join, rel.target]
        for resource in needed:
            if not await self.can(ctx.actor, resource, "list"):
                return dict.fromkeys(parent_ids)
        tenant = ctx.extras.get("requested_tenant")
        out: dict[Any, Any] = {}
        try:
            if kind == "reverse" and len(parent_ids) > 1:
                batch = await self._reverse_batch(ctx, rel, parent_ids, limit, nested)
                if batch is not None:
                    return batch
            for parent_id in parent_ids:
                if kind == "reverse":
                    page = await self.list(
                        rel.owner,
                        ListQuery(limit=limit),
                        actor=ctx.actor,
                        tenant=tenant,
                        expand=nested,
                        _within={rel.field: parent_id},
                    )
                else:
                    page = await self._linked(
                        rel, parent_id, ListQuery(limit=limit), ctx.actor, tenant, nested
                    )
                out[parent_id] = RelatedPage(page.items, page.total, limit)
        except (PermissionDenied, TenantRequired, TenantMismatch):
            return dict.fromkeys(parent_ids)
        return out

    async def _reverse_batch(
        self,
        ctx: OperationContext,
        rel: ReferenceDefinition,
        parent_ids: list[Any],
        limit: int,
        nested: Sequence[str],
    ) -> dict[Any, Any] | None:
        """Every parent's page from one query; ``None`` to fall back to one
        query per parent (too many records, no conditions, hidden field)."""
        from resource_engine.application.relations import RelatedPage
        from resource_engine.authz.fields import hidden_fields

        owner = self.registry.get(rel.owner)
        repository_supports = await self._supports_conditions(owner.name)
        if not repository_supports:
            return None
        page = await self.list(
            rel.owner,
            ListQuery(limit=MAX_RELATED_MATCHES + 1),
            actor=ctx.actor,
            tenant=ctx.extras.get("requested_tenant"),
            expand=nested,
            _condition={"in": [{"attr": f"resource.{rel.field}"}, list(parent_ids)]},
            _unbounded=True,
        )
        if page.total > MAX_RELATED_MATCHES:
            return None
        grouped: dict[Any, list[Any]] = {p: [] for p in parent_ids}
        for item in page.items:
            if rel.field in hidden_fields(item) or not hasattr(item, rel.field):
                return None  # cannot tell whose it is
            grouped.setdefault(getattr(item, rel.field), []).append(item)
        return {
            p: RelatedPage(items[:limit], len(items), limit)
            for p, items in grouped.items()
            if p in parent_ids or items
        }

    async def _supports_conditions(self, resource: str) -> bool:
        async with self._reading() as uow:
            return bool(getattr(uow.repository(resource), "supports_conditions", False))

    async def _linked(
        self,
        link: Any,
        source_id: Any,
        query: ListQuery | None,
        actor: Any,
        tenant: str | None,
        expand: Sequence[str],
    ) -> Page[Any]:
        """The ``link.target`` records linked to ``source_id`` through the join
        resource, with the caller's permissions on both."""
        target = self.registry.get(link.target)
        rows = await self.list(
            link.join,
            ListQuery(limit=MAX_RELATED_MATCHES + 1),
            actor=actor,
            tenant=tenant,
            _within={link.source_field: source_id},
            _unbounded=True,
        )
        if rows.total > MAX_RELATED_MATCHES:
            raise InvalidQuery(
                f"More than {MAX_RELATED_MATCHES} {link.join} records link this {link.source}",
                hint=f"List {link.join} filtered by {link.source_field} instead.",
                doc="docs/relationships.md#many-to-many",
            )
        from resource_engine.application.relations import unique

        ids = unique(getattr(row, link.target_field, None) for row in rows.items)
        query = query or ListQuery()
        if not ids:
            return Page([], 0, query.offset, query.limit)
        return await self.list(
            link.target,
            query,
            actor=actor,
            tenant=tenant,
            expand=expand,
            _condition={"in": [{"attr": f"resource.{target.id_field}"}, ids]},
        )

    async def get_many(
        self,
        resource: ResourceRef,
        ids: Collection[Any],
        *,
        actor: Any = None,
        tenant: str | None = None,
        expand: Sequence[str] = (),
    ) -> dict[Any, Any]:
        """The records with these ids that ``actor`` may read, by id, in one
        query where the repository supports it (batch loaders, GraphQL).
        Missing and unreadable ids are left out; nothing is audited."""
        definition = self.registry.get(resource)
        self._require(definition, "get")
        self._check_expand(definition, expand)
        ids = [i for i in dict.fromkeys(ids) if i is not None]
        if not ids or not await self.can(actor, definition.name, "get", tenant=tenant):
            return {}
        ctx = self._context(definition, "get", actor=actor, tenant=tenant)
        async with self._reading(ctx):
            return await self._records(ctx, definition, ids, nested=expand)

    async def list_related(
        self,
        resource: ResourceRef,
        entity_id: Any,
        relation: str,
        query: ListQuery | None = None,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expand: Sequence[str] = (),
    ) -> Page[Any]:
        """The records of a reverse relation or a many-to-many link:
        ``list_related("customers", 7, "invoices")`` lists the invoices of
        customer 7, ``list_related("invoices", 3, "tags")`` the tags of invoice 3.
        The record must be readable by the caller (``get``); the related records
        are listed with the caller's ``list`` permissions, filters and ordering."""
        definition = self.registry.get(resource)
        found = self._relation(definition, relation)
        if found is None or found[0] == "forward":
            raise InvalidQuery(
                f"{definition.name} has no reverse or many-to-many relation {relation!r}",
                hint=f"Relations: {list(self.reverse.get(definition.name, {}))}",
                doc="docs/relationships.md#reverse-relations",
            )
        kind, rel, _related = found
        await self.get(definition.name, entity_id, actor=actor, tenant=tenant)
        if kind == "link":
            return await self._linked(rel, entity_id, query, actor, tenant, expand)
        return await self.list(
            rel.owner,
            query,
            actor=actor,
            tenant=tenant,
            expand=expand,
            _within={rel.field: entity_id},
        )

    async def _related_scope(
        self, ctx: OperationContext, query: ListQuery, related: Mapping[str, Mapping[str, Any]]
    ) -> ListQuery | None:
        """``list`` filters on related records → ``<field> in [ids]``; ``None``
        when no related record matches."""
        from resource_engine.authz.conditions import conjunction

        if not getattr(ctx.repository(), "supports_conditions", False):
            raise ConditionNotSupported(
                f"The repository of {ctx.resource.name!r} cannot filter by related records",
                hint="Use a repository with supports_conditions (in-memory, SQLAlchemy), or "
                "filter by the reference id.",
                doc="docs/relationships.md#filtering",
            )
        condition = query.condition
        for name, filters in related.items():
            ref = ctx.resource.references[name]
            target = self.registry.get(ref.target)
            tctx = await self._related_context(ctx, target)
            if tctx is None:
                return None
            await self._gate_query(tctx, ListQuery(filters=filters), await self._read_rules(tctx))
            own = dict(filters)
            if target.tenant_field and tctx.tenant is not None:
                own[target.tenant_field] = tctx.tenant
            page = await ctx.uow.repository(target.name).list(  # type: ignore[union-attr]
                ListQuery(filters=own, limit=MAX_RELATED_MATCHES + 1)
            )
            if page.total > MAX_RELATED_MATCHES or len(page.items) > MAX_RELATED_MATCHES:
                raise InvalidQuery(
                    f"The filter on {name} matches more than {MAX_RELATED_MATCHES} "
                    f"{target.name} records",
                    hint="Narrow the related filter, or filter by the reference id.",
                    doc="docs/relationships.md#filtering",
                )
            ids = [getattr(e, target.id_field) for e in page.items if await self._visible(tctx, e)]
            if not ids:
                return None
            condition = conjunction(condition, {"in": [{"attr": f"resource.{ref.field}"}, ids]})
        return dataclasses.replace(query, condition=condition)

    async def _release_dependents(
        self, ctx: OperationContext, dependents: Sequence[ReferenceDefinition]
    ) -> None:
        """Delete: refuse while restricting references exist (RE-E109); clear
        or delete the records of ``set_null`` / ``cascade`` references, through
        the manager as the same caller."""
        entity_id = ctx.entity_id
        for ref in dependents:
            if ref.on_delete != "restrict":
                continue
            page = await ctx.uow.repository(ref.owner).list(  # type: ignore[union-attr]
                ListQuery(filters={ref.field: entity_id}, limit=1)
            )
            if page.items or page.total:
                raise ReferenceInUse(
                    f"{ctx.resource.name} {entity_id!r} is referenced by {ref.owner}.{ref.field}",
                    hint=f"Delete or change those {ref.owner} records first.",
                    details={"resource": ref.owner, "field": ref.field, "id": entity_id},
                    doc="docs/relationships.md#deleting",
                )
        tenant = ctx.extras.get("requested_tenant")
        for ref in dependents:
            if ref.on_delete == "restrict":
                continue
            owner = self.registry.get(ref.owner)
            done: set[Any] = set()
            while True:
                page = await ctx.uow.repository(ref.owner).list(  # type: ignore[union-attr]
                    ListQuery(filters={ref.field: entity_id}, limit=100)
                )
                pending = [i for e in page.items if (i := getattr(e, owner.id_field)) not in done]
                if not pending:
                    break
                for child_id in pending:
                    done.add(child_id)
                    if ref.on_delete == "cascade":
                        await self.delete(ref.owner, child_id, actor=ctx.actor, tenant=tenant)
                    else:
                        await self.update(
                            ref.owner, child_id, {ref.field: None}, actor=ctx.actor, tenant=tenant
                        )

    # -------------------------------------------------------------------- write

    @traced("create")
    @idempotent("create")
    async def create(
        self,
        resource: ResourceRef,
        data: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        idempotency_key: str | None = None,
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
            if definition.parent is not None:  # a subtype: its base's discriminator
                setattr(entity, definition.parent.discriminator or "", definition.identity)
            for runner in definition.record_machines.values():
                await runner.initialize(entity)
                _stamp_state(runner, entity)
            if definition.version_field and getattr(entity, definition.version_field, None) is None:
                setattr(entity, definition.version_field, 1)
            await self._stamp_tenant(ctx, entity)
            await self._gate(ctx, "create", resource=entity, input=ctx.data)
            await self._gate_fields(ctx, entity, ctx.data)
            await hooks.run(HookPoint.BEFORE_CREATE, ctx)
            await self._check_references(ctx, ctx.entity)
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

    @traced("update")
    @idempotent("update")
    async def update(
        self,
        resource: ResourceRef,
        entity_id: Any,
        data: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
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
            await self._check_references(ctx, entity, changes)
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

    @traced("delete")
    @idempotent("delete")
    async def delete(
        self,
        resource: ResourceRef,
        entity_id: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
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
        dependents = tuple(
            ref
            for ref in (
                *self.dependents.get(definition.name, ()),
                *(self.dependents.get(definition.parent.name, ()) if definition.parent else ()),
            )
            if self.registry.get(ref.owner).view is None  # view rows follow their sources
        )
        # set_null / cascade change other records: one transaction for all of them
        grouped = any(r.on_delete != "restrict" for r in dependents)
        async with (
            self.transaction() if grouped else contextlib.nullcontext(),
            self._transaction(ctx) as uow,
        ):
            entity = await self._load(ctx)
            await self._gate_record(ctx, "delete", entity)
            await self._check_version(ctx, entity)
            await hooks.run(HookPoint.BEFORE_DELETE, ctx)
            await self._release_dependents(ctx, dependents)
            await self._remove(ctx, entity)
            if self.documents is not None and definition.document_slots:
                await self.documents.cascade(ctx)
            ctx.record_event(ResourceDeleted(resource=definition.name, entity_id=entity_id))
            await hooks.run(HookPoint.AFTER_DELETE, ctx)
            await self._before_commit(ctx, uow)
            await uow.commit()
        await self._after_commit(ctx)

    @traced("transition")
    @idempotent("transition")
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
        idempotency_key: str | None = None,
    ) -> Any:
        definition = self.registry.get(resource)
        if definition.machine is None:
            raise UnknownTransition(
                f"Resource {definition.name!r} has no state machine",
                hint=(
                    f"Transitions of {definition.name} records run on {definition.parent.name}."
                    if definition.parent is not None
                    else "Declare `state_machine = <StateMachine subclass>` on the resource."
                ),
            )
        machine = definition.machine_for(event)
        if machine is None:
            raise UnknownTransition(
                f"Resource {definition.name!r} has no transition {event!r}",
                hint=f"Available transitions: {', '.join(definition.transitions)}",
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
            _stamp_state(machine, entity)
            ctx.previous_state, ctx.new_state = previous, new
            ctx.entity = await self._write(ctx, entity)
            ctx.record_event(
                TransitionExecuted(
                    resource=definition.name,
                    entity_id=entity_id,
                    transition=event,
                    source=previous,
                    target=new,
                    machine=machine.name,
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
            events = [
                e for m in definition.machines.values() for e in await m.enabled_events(entity)
            ]
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

    @traced("history")
    async def history(
        self,
        resource: ResourceRef,
        entity_id: Any,
        *,
        actor: Any = None,
        tenant: str | None = None,
        machine: str | None = None,
    ) -> list[StateChange]:
        """The record's state changes, oldest first: its creation and every
        committed transition, with the actor and time. Read from the audit trail;
        the caller needs to be able to ``get`` the record (RE-E009 without an
        audit trail or a state machine). With several machines each change names
        its ``machine``; ``machine=`` keeps one."""
        from resource_engine.audit.history import read_history, require_history_resource

        definition = self.registry.get(resource)
        audit_resource = require_history_resource(self, definition)
        ctx = self._context(definition, "get", actor=actor, entity_id=entity_id, tenant=tenant)
        async with self._reading(ctx) as uow:
            entity = await self._load(ctx)
            await self._gate_record(ctx, "get", entity)
            tenant_value = (
                getattr(entity, definition.tenant_field, None) if definition.tenant_field else None
            )
            return await read_history(
                uow, audit_resource, definition, entity_id, tenant_value, machine
            )

    @property
    def has_history(self) -> bool:
        """Whether ``history`` can read an audit trail."""
        from resource_engine.audit.history import history_resource

        name = history_resource(self.audit)
        return name is not None and name in {d.name for d in self.registry}

    @traced("action")
    @idempotent("action")
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
        idempotency_key: str | None = None,
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


def _stamp_state(machine: Any, entity: Any) -> None:
    """When the record entered the machine's current state (its ``since_field``)."""
    if machine.since_field:
        from datetime import UTC, datetime

        setattr(entity, machine.since_field, datetime.now(UTC))


async def _run_callbacks(callbacks: Any, kind: str, ctx: OperationContext) -> None:
    for callback in list(callbacks):
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            await report_error(
                exc,
                where=f"{kind}_callback",
                force=True,
                callback=getattr(callback, "__qualname__", repr(callback)),
                resource=ctx.resource.name,
                correlation_id=ctx.correlation_id,
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
