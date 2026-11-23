"""Inbound side: route envelopes received from a broker to handlers."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from resource_engine.domain.events import DomainEvent
from resource_engine.errors import NoHandler
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import MessageHandler


class EventRouter:
    """Dispatches envelopes by ``type``.

    Broker consumers (SQS poller, Service Bus receiver, Pub/Sub subscriber...) call
    ``dispatch`` and acknowledge the message only if it returns without error.
    Handlers typically call ``ResourceManager`` methods (an action, a transition).
    """

    def __init__(self, *, ignore_unknown: bool = False) -> None:
        self._handlers: dict[str, list[MessageHandler]] = {}
        self.ignore_unknown = ignore_unknown

    def register(
        self, event_type: str | type[DomainEvent], handler: MessageHandler
    ) -> MessageHandler:
        name = event_type if isinstance(event_type, str) else event_type.__name__
        self._handlers.setdefault(name, []).append(handler)
        return handler

    def on(self, event_type: str | type[DomainEvent]) -> Callable[[MessageHandler], MessageHandler]:
        """Decorator form of ``register``."""
        return lambda handler: self.register(event_type, handler)

    @property
    def event_types(self) -> list[str]:
        return list(self._handlers)

    async def dispatch(self, envelope: EventEnvelope) -> None:
        handlers = self._handlers.get(envelope.type)
        if not handlers:
            if self.ignore_unknown:
                return
            raise NoHandler(
                f"No handler registered for event type {envelope.type!r}",
                hint=f"Registered types: {', '.join(self._handlers) or '(none)'}",
            )
        for handler in handlers:
            result = handler(envelope)
            if inspect.isawaitable(result):
                await result
