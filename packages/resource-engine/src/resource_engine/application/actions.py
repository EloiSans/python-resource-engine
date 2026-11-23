"""Business actions (use cases) attached to a resource."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class Action:
    """A business operation that is neither CRUD nor a plain state transition.

    Subclass it, document it with a docstring and implement ``execute``. The engine
    validates the input, opens the unit of work, loads the entity (for instance
    actions), runs ``before_action`` / ``after_action`` hooks, commits and publishes
    events. ``execute`` may be sync or async.

    Class attributes:
        name: Public name (defaults to the snake_case class name).
        input_schema: Pydantic model for the input, or ``None``.
        output_schema: Pydantic model for the result. If ``None`` the resource's
            ``read`` schema is used for instance actions returning the entity.
        instance: ``True`` if the action targets one entity (``/{id}/actions/x``),
            ``False`` for collection-level actions (``/actions/x``).
    """

    name: ClassVar[str | None] = None
    input_schema: ClassVar[type[BaseModel] | None] = None
    output_schema: ClassVar[type[BaseModel] | None] = None
    instance: ClassVar[bool] = True

    @classmethod
    def action_name(cls) -> str:
        return cls.name or _snake(cls.__name__)

    def execute(self, ctx: OperationContext) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError
