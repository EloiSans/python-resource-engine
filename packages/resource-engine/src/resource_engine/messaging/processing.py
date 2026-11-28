"""What to do with one inbound message: handle, skip, retry or dead-letter.

``MessageProcessor`` is shared by pull consumers (``MessageConsumer``) and push
endpoints (``resource_engine_fastapi.build_event_receiver``), so both behave the
same way.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from resource_engine.domain.exceptions import DomainError
from resource_engine.errors import (
    ConfigurationError,
    EmailRejected,
    InboxNotSupported,
    MessageRejected,
    NoHandler,
    TemplateFailed,
    TemplateNotFound,
    TransactionDoomed,
    ValidationFailed,
)
from resource_engine.logs import bind_log_context
from resource_engine.messaging.consumer import EventRouter
from resource_engine.ports.messaging import (
    MessageHandler,
    MessageObserver,
    ReceivedMessage,
    SupportsInbox,
)
from resource_engine.ports.unit_of_work import UnitOfWork
from resource_engine.tracing import get_tracer

if TYPE_CHECKING:
    from resource_engine.application.manager import ResourceManager

log = logging.getLogger("resource_engine.messaging")

#: Errors that will fail again on every delivery: dead-letter them at once.
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    NoHandler,
    MessageRejected,
    DomainError,
    ValidationFailed,
    EmailRejected,
    TemplateNotFound,
    TemplateFailed,
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
    """What to do with a message.

    ``blocked`` is true when the handler did not run because an earlier message
    with the same key failed in this batch (``outcome`` is then ``RETRY``).
    """

    outcome: Outcome
    error: str | None = None
    delay_seconds: float = 0.0
    blocked: bool = False
    exception: BaseException | None = field(default=None, compare=False, repr=False)


class MessageProcessor:
    """Runs the handler for one message and decides its outcome.

    Args:
        handler: An ``EventRouter`` or a single ``async (envelope) -> None``.
        name: Consumer name; inbox entries are kept per consumer, so two consumers
            of the same event each process it once.
        manager: Run each message in ``manager.transaction()``: the inbox check,
            every manager call of the handler and the inbox mark commit together,
            so a message has its effect once. Its unit of work needs ``inbox()``.
        inbox: Without ``manager``: unit of work factory whose units expose
            ``inbox()``. Already-processed envelope ids are acknowledged without
            running the handler, but the mark is written after the handler, in
            its own transaction: a crash between the two can run a handler twice.
        retry: Backoff and delivery limit.
        permanent_errors: Exceptions that dead-letter immediately.
        observers: Called after every message with ``(consumer, message, result,
            seconds)`` (metrics); failures are logged and ignored.
    """

    def __init__(
        self,
        handler: EventRouter | MessageHandler,
        *,
        name: str,
        manager: ResourceManager | None = None,
        inbox: Callable[[], UnitOfWork] | None = None,
        retry: RetryPolicy | None = None,
        permanent_errors: tuple[type[BaseException], ...] = PERMANENT_ERRORS,
        observers: Sequence[MessageObserver] = (),
    ) -> None:
        if manager is not None and inbox is not None:
            raise ConfigurationError(
                "Pass either manager= (one transaction) or inbox=, not both",
                hint="With manager=, the inbox of the manager's unit of work is used.",
                doc="docs/messaging.md#exactly-once-effects",
            )
        self.dispatch = handler.dispatch if isinstance(handler, EventRouter) else handler
        self.name = name
        self.manager = manager
        self.inbox = inbox
        self.retry = retry or RetryPolicy()
        self.permanent_errors = permanent_errors
        self.observers = list(observers)

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
        started = time.perf_counter()
        envelope = message.envelope
        attributes: dict[str, Any] = {"resource_engine.consumer": self.name}
        if envelope is not None:
            attributes |= {
                "messaging.message.id": envelope.id,
                "resource_engine.event_type": envelope.type,
                "resource_engine.correlation_id": envelope.correlation_id or "",
            }
        if message.delivery_count is not None:
            attributes["messaging.message.delivery_count"] = message.delivery_count
        with bind_log_context(
            consumer=self.name,
            message_id=envelope.id if envelope else None,
            event_type=envelope.type if envelope else None,
            correlation_id=envelope.correlation_id if envelope else None,
        ):
            async with get_tracer().span(
                f"{self.name} process",
                kind="consumer",
                attributes=attributes,
                parent=envelope.trace if envelope is not None else None,
            ) as span:
                result = await self._process(message)
                span.set_attribute("resource_engine.outcome", result.outcome.value)
                if result.exception is not None:
                    from resource_engine.reporting import report_error

                    await report_error(
                        result.exception,
                        where="consumer",
                        force=result.outcome is Outcome.DEAD_LETTER,
                        outcome=result.outcome.value,
                        delivery_count=message.delivery_count,
                    )
                if result.outcome in (Outcome.RETRY, Outcome.DEAD_LETTER) and result.error:
                    span.record_error(result.error)
        await self.observe(message, result, time.perf_counter() - started)
        return result

    async def observe(
        self, message: ReceivedMessage, result: ProcessResult, seconds: float
    ) -> None:
        for observer in self.observers:
            try:
                outcome = observer(self.name, message, result, seconds)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:
                log.exception("Message observer %r failed", observer)

    def _failure(self, message: ReceivedMessage, exc: BaseException) -> ProcessResult:
        envelope = message.envelope
        assert envelope is not None
        cause = exc.__cause__ if isinstance(exc, TransactionDoomed) and exc.__cause__ else exc
        if isinstance(cause, self.permanent_errors):
            log.error("Message %s (%s) rejected: %s", envelope.id, envelope.type, cause)
            return ProcessResult(
                Outcome.DEAD_LETTER, f"{type(cause).__name__}: {cause}", exception=cause
            )
        error = f"{type(cause).__name__}: {cause}"
        if self.retry.exhausted(message.delivery_count):
            log.error("Message %s failed %s times: %s", envelope.id, message.delivery_count, error)
            return ProcessResult(Outcome.DEAD_LETTER, error, exception=cause)
        delay = self.retry.delay(message.delivery_count)
        log.warning("Message %s failed, retrying in %.1fs: %s", envelope.id, delay, error)
        return ProcessResult(Outcome.RETRY, error, delay, exception=cause)

    async def _transactional(self, message: ReceivedMessage) -> ProcessResult:
        """Inbox check, handler and inbox mark in one ``manager.transaction()``."""
        envelope = message.envelope
        assert envelope is not None and self.manager is not None

        class _Duplicate(Exception):
            pass

        try:
            async with self.manager.transaction() as tx:
                if not isinstance(tx.uow, SupportsInbox):
                    raise InboxNotSupported(
                        f"{type(tx.uow).__name__} has no inbox()",
                        hint="Pass inbox_table=inbox_table(metadata) to the SQLAlchemy unit "
                        "of work, or use inbox= without manager=.",
                    )
                inbox = tx.uow.inbox()
                if envelope.id in await inbox.processed(self.name, [envelope.id]):
                    raise _Duplicate
                result = self.dispatch(envelope)
                if inspect.isawaitable(result):
                    await result
                # a concurrent consumer may have committed it meanwhile: undo ours
                if envelope.id in await inbox.processed(self.name, [envelope.id]):
                    raise _Duplicate
                await inbox.mark_processed(self.name, [envelope.id])
        except _Duplicate:
            return ProcessResult(Outcome.DUPLICATE)
        except InboxNotSupported:
            raise
        except Exception as exc:  # handler, doomed transaction or commit (a racing consumer)
            return self._failure(message, exc)
        return ProcessResult(Outcome.ACK)

    async def _process(self, message: ReceivedMessage) -> ProcessResult:
        envelope = message.envelope
        if envelope is None:
            log.error("Undecodable message %s: %s", message.message_id, message.error)
            return ProcessResult(Outcome.DEAD_LETTER, f"undecodable: {message.error}")
        if self.manager is not None:
            return await self._transactional(message)

        if self.inbox is not None and envelope.id in await self._inbox_call(
            "processed", envelope.id
        ):
            return ProcessResult(Outcome.DUPLICATE)

        try:
            result = self.dispatch(envelope)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            return self._failure(message, exc)

        if self.inbox is not None:
            try:
                await self._inbox_call("mark_processed", envelope.id)
            except InboxNotSupported:
                raise
            except Exception:  # e.g. a concurrent consumer marked it first
                log.warning("Could not record %s in the inbox", envelope.id, exc_info=True)
        return ProcessResult(Outcome.ACK)


class ActivityMessageObserver:
    """``MessageObserver`` recording each consumer's handling of each message
    in an ``ActivityLog``, for the explorer's "who handled this event"
    (docs/explorer.md). Undecodable messages have no event and aren't recorded."""

    def __init__(self, log: Any) -> None:
        self.log = log

    async def __call__(
        self, consumer: str, message: ReceivedMessage, result: ProcessResult, seconds: float
    ) -> None:
        from resource_engine.ports.activity import DeliveryRecord

        envelope = message.envelope
        if envelope is None:
            return
        await self.log.record_delivery(
            DeliveryRecord(
                consumer=consumer,
                message_id=envelope.id,
                event_type=envelope.type,
                outcome=result.outcome.value,
                seconds=seconds,
                error=result.error,
            )
        )


class LoggingObserver:
    """``MessageObserver`` writing one log line per message (logger
    ``resource_engine.messaging.metrics``): outcome, type, key, delivery count
    and duration. A model for metrics adapters (OpenTelemetry, Prometheus)."""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        self.logger = logger or logging.getLogger("resource_engine.messaging.metrics")
        self.level = level

    def __call__(
        self, consumer: str, message: ReceivedMessage, result: ProcessResult, seconds: float
    ) -> None:
        envelope = message.envelope
        self.logger.log(
            self.level,
            "consumer=%s outcome=%s type=%s key=%s deliveries=%s seconds=%.4f",
            consumer,
            result.outcome.value,
            envelope.type if envelope else None,
            envelope.key if envelope else None,
            message.delivery_count,
            seconds,
        )
