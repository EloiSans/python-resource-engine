"""Field-level masking of read results.

Hidden fields are *omitted*: the returned model has no value for them (reading
the attribute raises ``AttributeError``) and ``model_dump`` leaves them out.
Entry adapters use ``hidden_fields`` to serialize without them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

_MARK = "_re_hidden_fields"


def mask(model: Any, hidden: Iterable[str]) -> Any:
    """A copy of ``model`` without ``hidden`` fields (``model`` itself when
    nothing is hidden or it is not a Pydantic model)."""
    hidden = frozenset(hidden)
    if not hidden or not isinstance(model, BaseModel):
        return model
    fields = type(model).model_fields
    hidden &= frozenset(fields)
    if not hidden:
        return model
    values = {k: v for k, v in model.__dict__.items() if k not in hidden}
    masked = type(model).model_construct(_fields_set=model.model_fields_set - hidden, **values)
    for name in hidden:
        masked.__dict__.pop(name, None)
    private = dict(masked.__pydantic_private__ or {})
    private[_MARK] = hidden
    object.__setattr__(masked, "__pydantic_private__", private)
    return masked


def hidden_fields(model: Any) -> frozenset[str]:
    """Fields ``mask`` removed from ``model`` (empty for unmasked values)."""
    private = getattr(model, "__pydantic_private__", None)
    if not isinstance(private, dict):
        return frozenset()
    return private.get(_MARK, frozenset())
