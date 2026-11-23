"""What to do with one inbound message: handle, skip, retry or dead-letter.

``MessageProcessor`` is shared by pull consumers (``MessageConsumer``) and push
endpoints (``resource_engine_fastapi.build_event_receiver``), so both behave the
same way.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from resource_engine.domain.exceptions import DomainError
from resource_engine.errors import (
    InboxNotSupported,
    MessageRejected,
    NoHandler,
    ValidationFailed,
)
from resource_engine.messaging.consumer import EventRouter
from resource_engine.ports.messaging import MessageHandler, ReceivedMessage, SupportsInbox
from resource_engine.ports.unit_of_work import UnitOfWork

log = logging.getLogger("resource_engine.messaging")

#: Errors that will fail again on every delivery: dead-letter them at once.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    NoHandler,
    MessageRejected,
    DomainError,
    ValidationFailed,
)


class Outcome(StrEnum):
    ACK = "ack"
    DUPLICATE = "duplicate"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"

    @property
    def settled(self) -> bool:
        """True when the message must be removed from the broker."""
        return self in (Outcome.ACK, Outcome.DUPLICATE)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with a delivery limit.

    ``delay(n)`` is the wait before delivery ``n + 1``. When ``delivery_count``
    reaches ``max_deliveries`` the message is dead-lettered. Brokers that do not
    report delivery counts rely on their own redrive policy instead.
    """

    max_deliveries: int = 5
    base_delay: float = 1.0
    factor: float = 2.0
    max_delay: float = 300.0

    def delay(self, delivery_count: int | None) -> float:
        n = max((delivery_count or 1) - 1, 0)
        return min(self.base_delay * self.factor**n, self.max_delay)

    def exhausted(self, delivery_count: int | None) -> bool:
        return delivery_count is not None and delivery_count >= self.max_deliveries


@dataclass(frozen=True)
class ProcessResult:
    outcome: Outcome
    error: str | None = None
    delay_seconds: float = 0.0


class MessageProcessor:
    """Runs the handler for one message and decides its outcome.

    Args:
        handler: An ``EventRouter`` or a single ``async (envelope) -> None``.
        name: Consumer name; inbox entries are kept per consumer, so two consumers
            of the same event each process it once.
        inbox: Unit of work factory whose units expose ``inbox()``. When given,
            already-processed envelope ids are acknowledged without running the
            handler. The mark is written after the handler succeeds, in its own
            transaction: a crash between the two can still run a handler twice,
            so keep handlers idempotent where possible.
        retry: Backoff and delivery limit.
        permanent_errors: Exceptions that dead-letter immediately.
    """

    def __init__(
        self,
        handler: EventRouter | MessageHandler,
        *,
        name: str,
        inbox: Callable[[], UnitOfWork] | None = None,
        retry: RetryPolicy | None = None,
        permanent_errors: tuple[type[BaseException], ...] = PERMANENT_ERRORS,
    ) -> None:
        self.dispatch = handler.dispatch if isinstance(handler, EventRouter) else handler
        self.name = name
        self.inbox = inbox
        self.retry = retry or RetryPolicy()
        self.permanent_errors = permanent_errors

    async def _inbox_call(self, method: str, message_id: str) -> Any:
        assert self.inbox is not None
        async with self.inbox() as uow:
            if not isinstance(uow, SupportsInbox):
                raise InboxNotSupported(
                    f"{type(uow).__name__} has no inbox()",
                    hint="Pass inbox_table=inbox_table(metadata) to the SQLAlchemy unit of "
                    "work, or drop inbox= to run without de-duplication.",
                )
            result = await getattr(uow.inbox(), method)(self.name, [message_id])
            if method == "mark_processed":
                await uow.commit()
            return result

    async def process(self, message: ReceivedMessage) -> ProcessResult:
        envelope = message.envelope
        if envelope is None:
            log.error("Undecodable message %s: %s", message.message_id, message.error)
            return ProcessResult(Outcome.DEAD_LETTER, f"undecodable: {message.error}")

        if self.inbox is not None and envelope.id in await self._inbox_call(
            "processed", envelope.id
        ):
            return ProcessResult(Outcome.DUPLICATE)

        try:
            result = self.dispatch(envelope)
            if inspect.isawaitable(result):
                await result
        except self.permanent_errors as exc:
            log.error("Message %s (%s) rejected: %s", envelope.id, envelope.type, exc)
            return ProcessResult(Outcome.DEAD_LETTER, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if self.retry.exhausted(message.delivery_count):
                log.error(
                    "Message %s failed %s times: %s", envelope.id, message.delivery_count, error
                )
                return ProcessResult(Outcome.DEAD_LETTER, error)
            delay = self.retry.delay(message.delivery_count)
            log.warning("Message %s failed, retrying in %.1fs: %s", envelope.id, delay, error)
            return ProcessResult(Outcome.RETRY, error, delay)

        if self.inbox is not None:
            try:
                await self._inbox_call("mark_processed", envelope.id)
            except InboxNotSupported:
                raise
            except Exception:  # e.g. a concurrent consumer marked it first
                log.warning("Could not record %s in the inbox", envelope.id, exc_info=True)
        return ProcessResult(Outcome.ACK)
