"""Idempotency keys for writes: a retried request returns the first result
instead of running again. See docs/idempotency.md.

``create``, ``update``, ``delete``, ``transition`` and ``run_action`` accept
``idempotency_key=``. The first call with a key runs normally and stores its
result in the same transaction as the change. A later call by the same caller
with the same key and the same request returns that result without running;
with a different request it fails (RE-E107). Two calls racing with one key
both run, but only one commits: the other rolls back and replays the winner.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, TypeAdapter

from resource_engine.errors import (
    ConfigurationError,
    IdempotencyKeyReused,
    IdempotencyNotSupported,
    ValidationFailed,
)
from resource_engine.ports.idempotency import IdempotencyRecord, SupportsIdempotency

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext

logger = logging.getLogger("resource_engine.idempotency")

KEY = re.compile(r"[\x21-\x7e]{1,255}")  # printable ASCII, no spaces


@dataclass
class _Request:
    scope: str
    key: str
    fingerprint: str
    resource: str
    operation: str
    ctx: OperationContext | None = None  # the operation's own context, once created


_pending: ContextVar[_Request | None] = ContextVar("resource_engine_idempotency", default=None)


def pending_for(ctx: OperationContext) -> _Request | None:
    """The idempotent request ``ctx`` belongs to (only the top operation's
    context, not those of operations its hooks start)."""
    request = _pending.get()
    if request is None:
        return None
    if request.ctx is None:
        request.ctx = ctx
    return request if request.ctx is ctx else None


def scope_of(actor: Any) -> str:
    from resource_engine.audit.recorder import actor_id

    return actor_id(actor) or "anonymous"


def fingerprint(operation: str, resource: str, arguments: dict[str, Any]) -> str:
    from resource_engine.audit.recorder import jsonable

    payload = {"operation": operation, "resource": resource, "arguments": arguments}
    text = json.dumps(jsonable(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _input(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_unset=True)
    return value


# ------------------------------------------------------------------- results


def encode(ctx: OperationContext, result: Any) -> dict[str, Any]:
    """The result as JSON, with what is needed to rebuild it."""
    from resource_engine.audit.recorder import jsonable
    from resource_engine.authz.fields import hidden_fields

    if result is None:
        return {"kind": "none"}
    if isinstance(result, BaseModel):
        hidden = sorted(hidden_fields(result))
        schema = "read" if type(result) is ctx.resource.schemas.get("read") else "output"
        return {
            "kind": "model",
            "schema": schema,
            # secrets shown once (an API key) are never stored for replays
            "data": result.model_dump(
                mode="json", exclude=set(hidden) | set(ctx.extras.get("never_store", ()))
            ),
            "hidden": hidden,
        }
    return {"kind": "json", "data": jsonable(result)}


def decode(definition: Any, operation: str, action: str | None, stored: dict[str, Any]) -> Any:
    from resource_engine.authz.fields import mask

    kind = stored.get("kind")
    if kind == "none":
        return None
    if kind == "json":
        return stored.get("data")
    if stored.get("schema") == "output":
        schema = definition.actions[action].output_schema if action else None
    else:
        schema = definition.schemas.get("read")
    if schema is None:
        return stored.get("data")
    data, hidden = stored.get("data", {}), stored.get("hidden", [])
    if not hidden:
        return schema.model_validate(data)
    values = {
        name: TypeAdapter(info.annotation).validate_python(data[name])
        for name, info in schema.model_fields.items()
        if name in data
    }
    return mask(schema.model_construct(**values), hidden)


# ---------------------------------------------------------------- decorator


def idempotent(operation: str) -> Callable[[Any], Any]:
    """Make a manager write accept ``idempotency_key=``."""

    def decorate(fn: Any) -> Any:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(manager: Any, *args: Any, **kwargs: Any) -> Any:
            key = kwargs.get("idempotency_key")
            if key is None:
                return await fn(manager, *args, **kwargs)
            if not isinstance(key, str) or not KEY.fullmatch(key):
                raise ValidationFailed(
                    "Invalid idempotency key: use 1 to 255 printable ASCII characters",
                    details=[{"loc": ["idempotency_key"], "msg": "invalid idempotency key"}],
                )
            bound = signature.bind(manager, *args, **kwargs)
            arguments = {
                name: _input(value)
                for name, value in bound.arguments.items()
                if name not in ("self", "actor", "idempotency_key")
            }
            definition = manager.registry.get(arguments.pop("resource"))
            request = _Request(
                scope=scope_of(kwargs.get("actor")),
                key=key,
                fingerprint=fingerprint(operation, definition.name, arguments),
                resource=definition.name,
                operation=operation,
            )
            action = arguments.get("action") if operation == "action" else None
            found = await _lookup(manager, request)
            if found is not None:
                return decode(definition, operation, action, found.result)
            token = _pending.set(request)
            try:
                return await fn(manager, *args, **kwargs)
            except Exception:
                found = await _lookup(manager, request)  # a racing duplicate committed first
                if found is None:
                    raise
                logger.info("Idempotency key %s replayed after a concurrent duplicate", key)
                return decode(definition, operation, action, found.result)
            finally:
                _pending.reset(token)

        return wrapper

    return decorate


async def _lookup(manager: Any, request: _Request) -> IdempotencyRecord | None:
    async with manager._reading() as uow:
        record = await _store(uow).get(request.scope, request.key)
    if record is None or (
        record.expires_at is not None and _aware(record.expires_at) <= datetime.now(UTC)
    ):
        return None
    if record.fingerprint != request.fingerprint:
        raise IdempotencyKeyReused(
            f"The idempotency key {request.key!r} was used for another request",
            hint="Use a new key for every distinct request; reuse a key only to retry "
            "exactly the same one.",
            details={
                "key": request.key,
                "first": {"resource": record.resource, "operation": record.operation},
            },
        )
    return record


async def store_result(manager: Any, ctx: OperationContext, uow: Any) -> None:
    """Step 9, before commit: store the result of an idempotent write."""
    request = pending_for(ctx)
    if request is None:
        return
    now = datetime.now(UTC)
    await _store(uow).put(
        IdempotencyRecord(
            scope=request.scope,
            key=request.key,
            fingerprint=request.fingerprint,
            resource=request.resource,
            operation=request.operation,
            result=encode(ctx, ctx.result),
            created_at=now,
            expires_at=now + manager.idempotency_ttl,
        )
    )


def _store(uow: Any) -> Any:
    if not isinstance(uow, SupportsIdempotency):
        raise IdempotencyNotSupported(
            f"{type(uow).__name__} has no idempotency store",
            hint="Use the in-memory unit of work, or pass idempotency_table= to the "
            "SQLAlchemy unit of work factory (engine_tables(metadata, idempotency=True)).",
            doc="docs/idempotency.md",
        )
    return uow.idempotency()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# ------------------------------------------------------------------ janitor


class IdempotencyJanitor:
    """Deletes expired idempotency records. Run ``run_once`` periodically (it is
    a ``JobRunner`` job)."""

    def __init__(self, uow_factory: Callable[[], Any], *, batch_size: int = 1000) -> None:
        if batch_size < 1:
            raise ConfigurationError("batch_size must be at least 1")
        self.uow_factory = uow_factory
        self.batch_size = batch_size

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        total = 0
        while True:
            async with self.uow_factory() as uow:
                deleted = await _store(uow).purge(now, self.batch_size)
                await uow.commit()
            total += deleted
            if deleted < self.batch_size:
                return total


DEFAULT_TTL = timedelta(hours=24)
