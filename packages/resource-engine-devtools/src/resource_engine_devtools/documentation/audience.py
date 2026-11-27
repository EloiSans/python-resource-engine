"""Who the generated documentation is for.

- ``technical`` (the default): everything, for developers and integrators:
  class paths, endpoints, schemas, hook functions, and the API pages
  (OpenAPI, AsyncAPI, GraphQL) with their documents.
- ``functional``: for people who don't read code (product owners, testers,
  support): what each resource is, its operations, states, rules, documents,
  emails and permissions, without class paths, endpoints, schemas or API files.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

AUDIENCES = ("technical", "functional")

_audience: ContextVar[str] = ContextVar("resource_engine_docs_audience", default="technical")


@contextmanager
def docs_audience(audience: str) -> Iterator[None]:
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown audience {audience!r}; use one of: {', '.join(AUDIENCES)}")
    token = _audience.set(audience)
    try:
        yield
    finally:
        _audience.reset(token)


def technical() -> bool:
    """Whether to show technical details (class paths, endpoints, schemas)."""
    return _audience.get() == "technical"


def current_audience() -> str:
    return _audience.get()
