"""Domain building blocks.

Nothing in this package may import FastAPI, SQLAlchemy, Pydantic or any
infrastructure library. Modules' ``domain/`` packages should only depend on this.
"""

from resource_engine.domain.events import DomainEvent
from resource_engine.domain.exceptions import DomainError

__all__ = ["DomainError", "DomainEvent"]
