"""Lifecycle events emitted by the engine itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from resource_engine.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class ResourceEvent(DomainEvent):
    resource: str
    entity_id: Any


@dataclass(frozen=True, kw_only=True)
class ResourceCreated(ResourceEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class ResourceUpdated(ResourceEvent):
    changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ResourceDeleted(ResourceEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class TransitionExecuted(ResourceEvent):
    transition: str
    source: Any
    target: Any


@dataclass(frozen=True, kw_only=True)
class ActionExecuted(ResourceEvent):
    action: str
