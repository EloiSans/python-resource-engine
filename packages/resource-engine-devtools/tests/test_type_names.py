"""Types are rendered the same on every Python version (``str()`` of typing
objects changed between 3.12 and 3.14)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional, Union

import pytest
from resource_engine_devtools.documentation.introspector import _type_name


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str, "str"),
        (Decimal, "Decimal"),
        (str | None, "str | None"),
        (Optional[date], "date | None"),  # noqa: UP045
        (Union[int, str], "int | str"),  # noqa: UP007
        (None | int, "int | None"),
        (Literal["allow", "deny"], "Literal['allow', 'deny']"),
        (Optional[Literal["allow", "deny"]], "Literal['allow', 'deny'] | None"),  # noqa: UP045
        (list[str], "list[str]"),
        (dict[str, Any], "dict[str, Any]"),
        (tuple[int, ...], "tuple[int, ...]"),
        (Annotated[int, "meta"], "int"),
        (Any, "Any"),
        (type(None), "None"),
    ],
)
def test_type_names(annotation, expected):
    assert _type_name(annotation) == expected
