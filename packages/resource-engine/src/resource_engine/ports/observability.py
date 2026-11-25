"""Operation metrics port: an observer called after every manager operation.

``ResourceManager(observers=[...])`` calls each observer once per operation
(``list``, ``get``, ``create``... and the document operations), whether it
succeeded or failed. The OpenTelemetry implementation is
``resource_engine_opentelemetry.OperationMetrics``.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Literal, Protocol

type OperationOutcome = Literal["ok", "failed"]


@dataclass(frozen=True)
class OperationRun:
    """One finished operation.

    Attributes:
        resource: The resource's name.
        operation: ``list``, ``get``, ``create``, ``update``, ``delete``,
            ``transition``, ``action``, ``attach``, ``detach``,
            ``request_upload``, ``confirm_upload`` or ``list_documents``.
        outcome: ``ok`` or ``failed``.
        seconds: Wall time, from the call to its return (after commit and
            post-commit work).
        action: ``transition:<event>`` or ``action:<name>`` (else ``None``).
        error_type: The error code (``RE-E702``, a ``DomainError`` code) or the
            exception's class name, when failed.
        correlation_id: The operation's correlation id (``None`` when it failed
            before its context existed, e.g. an unknown resource).
        error: The exception, when failed.
    """

    resource: str
    operation: str
    outcome: OperationOutcome
    seconds: float
    action: str | None = None
    error_type: str | None = None
    correlation_id: str | None = None
    error: BaseException | None = None


class OperationObserver(Protocol):
    def __call__(self, run: OperationRun) -> Awaitable[None] | None: ...
