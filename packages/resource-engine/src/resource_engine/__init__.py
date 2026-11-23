"""Resource Engine: declarative resources for business backends.

Public API. Import from here rather than from submodules; submodule layout may
change before 1.0.
"""

from resource_engine.application.actions import Action
from resource_engine.application.context import OperationContext
from resource_engine.application.events import (
    ActionExecuted,
    ResourceCreated,
    ResourceDeleted,
    ResourceEvent,
    ResourceUpdated,
    TransitionExecuted,
)
from resource_engine.application.hooks import HookPoint, external_effect
from resource_engine.application.manager import ResourceManager
from resource_engine.application.query import ListQuery, Page
from resource_engine.application.registry import ResourceRegistry
from resource_engine.application.resource import (
    CRUD_OPERATIONS,
    ActionDefinition,
    Resource,
    ResourceDefinition,
)
from resource_engine.application.slots import DocumentSlot
from resource_engine.application.state_machines import describe_machine
from resource_engine.domain import DomainError, DomainEvent
from resource_engine.errors import ResourceEngineError

__version__ = "0.1.0.dev0"

__all__ = [
    "CRUD_OPERATIONS",
    "Action",
    "ActionDefinition",
    "ActionExecuted",
    "DocumentSlot",
    "DomainError",
    "DomainEvent",
    "HookPoint",
    "ListQuery",
    "OperationContext",
    "Page",
    "Resource",
    "ResourceCreated",
    "ResourceDefinition",
    "ResourceDeleted",
    "ResourceEngineError",
    "ResourceEvent",
    "ResourceManager",
    "ResourceRegistry",
    "ResourceUpdated",
    "TransitionExecuted",
    "describe_machine",
    "external_effect",
]
