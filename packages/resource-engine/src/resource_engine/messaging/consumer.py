"""Inbound side: route envelopes received from a broker to handlers."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter, ValidationError

from resource_engine.domain.events import DomainEvent
from resource_engine.errors import ConfigurationError, MessageRejected, NoHandler
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import MessageHandler


class EventRouter:
    """Dispatches envelopes by ``type``.

    Broker consumers (SQS poller, Service Bus receiver, Pub/Sub subscriber...) call
    ``dispatch`` and acknowledge the message only if it returns without error.
    Handlers typically call ``ResourceManager`` methods (an action, a transition).

    A handler registered with ``payload=`` (a Pydantic model, dataclass or any
    type Pydantic validates) gets the validated payload as a second argument:
    ``handler(envelope, payload)``. A payload that does not validate raises
    ``MessageRejected`` (RE-E406), so the message is dead-lettered without
    retries. The payload types also describe the consumed events in the
    AsyncAPI document.

    Args:
        ignore_unknown: Acknowledge types without a handler instead of
            raising ``NoHandler``.
        name: Consumer name used in the AsyncAPI document (its channel).
        description: What this consumer does, for the AsyncAPI document.
    """

    def __init__(
        self,
        *,
        ignore_unknown: bool = False,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        self._handlers: dict[str, list[tuple[MessageHandler, bool]]] = {}
        #: Event type → the payload version its handlers are written for.
        self._versions: dict[str, str] = {}
        self._payloads: dict[str, Any] = {}
        self._adapters: dict[str, TypeAdapter[Any]] = {}
        self.ignore_unknown = ignore_unknown
        self.name = name
        self.description = description

    def register(
        self,
        event_type: str | type[DomainEvent],
        handler: MessageHandler,
        *,
        payload: Any = None,
        schema_version: int | str | None = None,
    ) -> MessageHandler:
        """``schema_version``: the payload version the handler is written for;
        other versions of the type (published side by side) are acknowledged
        without calling it. ``None``: every version."""
        name = event_type if isinstance(event_type, str) else event_type.__name__
        if schema_version is not None:
            known_version = self._versions.get(name)
            if known_version is not None and known_version != str(schema_version):
                raise ConfigurationError(
                    f"Event type {name!r} is registered for two schema versions",
                    hint="Handle one version of an event per router.",
                    doc="docs/versioning.md#events",
                )
            self._versions[name] = str(schema_version)
        if payload is not None:
            known = self._payloads.get(name)
            if known is not None and known is not payload:
                raise ConfigurationError(
                    f"Event type {name!r} is registered with two payload types",
                    hint="Use one payload model per event type.",
                    doc="docs/messaging.md#typed-payloads",
                )
            self._payloads[name] = payload
            self._adapters[name] = TypeAdapter(payload)
        self._handlers.setdefault(name, []).append((handler, payload is not None))
        return handler

    def include(self, other: EventRouter) -> EventRouter:
        """Add ``other``'s handlers (and payload types) to this router, so one
        consumer or one outbox feed serves both: ``processes.include(mailer.router())``.
        Returns ``self``."""
        for event_type, handlers in other._handlers.items():
            for handler, typed in handlers:
                payload = other._payloads.get(event_type) if typed else None
                self.register(
                    event_type,
                    handler,
                    payload=payload,
                    schema_version=other._versions.get(event_type),
                )
        return self

    def on(
        self,
        event_type: str | type[DomainEvent],
        *,
        payload: Any = None,
        schema_version: int | str | None = None,
    ) -> Callable[[MessageHandler], MessageHandler]:
        """Decorator form of ``register``."""
        return lambda handler: self.register(
            event_type, handler, payload=payload, schema_version=schema_version
        )

    def schema_version(self, event_type: str) -> str | None:
        """The payload version handled for ``event_type`` (``None``: any)."""
        return self._versions.get(event_type)

    @property
    def event_types(self) -> list[str]:
        return list(self._handlers)

    def payload_type(self, event_type: str) -> Any:
        """The payload type registered for ``event_type``, or ``None``."""
        return self._payloads.get(event_type)

    def handlers(self, event_type: str) -> list[MessageHandler]:
        return [h for h, _ in self._handlers.get(event_type, ())]

    def parse(self, envelope: EventEnvelope) -> Any:
        """The envelope's payload validated against its registered type."""
        adapter = self._adapters.get(envelope.type)
        if adapter is None:
            return envelope.payload
        try:
            return adapter.validate_python(dict(envelope.payload))
        except ValidationError as exc:
            raise MessageRejected(
                f"Invalid payload for {envelope.type!r}",
                hint="The producer sent a payload that does not match the consumer's "
                "model; fix the producer or the model, then replay the dead letter.",
                details=exc.errors(include_url=False, include_context=False),
            ) from exc

    async def dispatch(self, envelope: EventEnvelope) -> None:
        handlers = self._handlers.get(envelope.type)
        if not handlers:
            if self.ignore_unknown:
                return
            raise NoHandler(
                f"No handler registered for event type {envelope.type!r}",
                hint=f"Registered types: {', '.join(self._handlers) or '(none)'}",
            )
        wanted = self._versions.get(envelope.type)
        if wanted is not None and envelope.schema_version != wanted:
            return  # another version of the event, published for other consumers
        payload = self.parse(envelope) if envelope.type in self._adapters else None
        for handler, typed in handlers:
            result = handler(envelope, payload) if typed else handler(envelope)  # type: ignore[call-arg]
            if inspect.isawaitable(result):
                await result
