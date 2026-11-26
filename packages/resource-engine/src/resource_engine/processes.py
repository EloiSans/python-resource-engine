"""Process managers: long-running processes across resources, as resources.

A process is a ``Resource`` whose records are its running instances. Its
state machine holds the steps, its timers the timeouts, its hooks the
compensations. It reacts to events (the engine's, its own domain events, other
services') with handlers that move the instance and act on other resources
through the manager:

```python
class InvoicePaymentProcess(Process):
    model = InvoicePayment
    schemas = {"create": PaymentStart, "read": PaymentRead}
    operations = ["list", "get", "create"]
    state_machine = PaymentFlow
    correlation = "invoice_id"
    reacts = [
        On("TransitionExecuted", start_payment, resource="invoices", transition="approve",
           start=True),
        On("PaymentReceived", record_payment, key="invoice_id", payload=PaymentReceived),
    ]
```

``ProcessRunner(manager).router()`` is the ``EventRouter`` of every process;
run it with ``MessageProcessor(router, name=..., manager=manager)``
(one transaction with the inbox: exactly-once effects) from a broker
consumer, or from the outbox with ``ProcessFeed``. See docs/processes.md.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import TypeAdapter

from resource_engine.application.query import ListQuery
from resource_engine.application.resource import Resource, ResourceDefinition
from resource_engine.domain.events import DomainEvent
from resource_engine.errors import ConfigurationError, PublishFailed
from resource_engine.messaging.consumer import EventRouter
from resource_engine.messaging.envelope import EventEnvelope
from resource_engine.ports.messaging import ReceivedMessage

if TYPE_CHECKING:
    from resource_engine.application.manager import ResourceManager
    from resource_engine.messaging.processing import MessageProcessor

logger = logging.getLogger("resource_engine.processes")
DOC = "docs/processes.md"

type StepHandler = Callable[[ProcessStep], Awaitable[None] | None]


@dataclass(frozen=True)
class On:
    """A process reacts to an event.

    Args:
        event: Event type (``"PaymentReceived"``) or a ``DomainEvent`` class.
        handler: ``(step: ProcessStep) -> None`` (sync or async).
        key: Where the correlation key is in the payload (dotted path; default
            ``entity_id``, the record of an engine event), or ``(envelope) -> key``.
        resource / transition / action: Only engine events of this resource,
            transition or action.
        start: Run the handler even when no instance matches the key (to start
            one with ``step.start()``). Otherwise such events are ignored.
        payload: Validate the payload against this model; ``step.payload`` is
            then the parsed value (invalid payloads are dead-lettered).
        description: What the step does, for the generated docs (default: the
            handler's docstring).
    """

    event: str | type[DomainEvent]
    handler: StepHandler
    key: str | Callable[[EventEnvelope], Any] = "entity_id"
    resource: str | None = None
    transition: str | None = None
    action: str | None = None
    start: bool = False
    payload: Any = None
    description: str | None = None

    @property
    def event_type(self) -> str:
        return self.event if isinstance(self.event, str) else self.event.__name__

    def matches(self, envelope: EventEnvelope) -> bool:
        payload = envelope.payload
        if self.resource is not None and (envelope.resource or payload.get("resource")) != (
            self.resource
        ):
            return False
        if self.transition is not None and payload.get("transition") != self.transition:
            return False
        return self.action is None or payload.get("action") == self.action

    def key_of(self, envelope: EventEnvelope) -> Any:
        if callable(self.key):
            return self.key(envelope)
        value: Any = envelope.payload
        for part in self.key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value


class Process(Resource):
    """A process manager: a resource whose records are running instances.

    Attributes:
        correlation: Field holding the key that ties events to an instance
            (``invoice_id``); one instance per key. It must be in the create
            and read schemas; ``step.start()`` sets it.
        reacts: ``On`` declarations: the events that move the process.

    By default only code creates instances (``internal_operations =
    ("create",)``) and transitions are not exposed by entry adapters: the
    process moves itself. Its records are readable through the API like any
    resource (``list``, ``get``, ``history``), with the usual permissions.
    """

    correlation: ClassVar[str]
    reacts: ClassVar[Sequence[On]] = ()
    internal_operations: ClassVar[Sequence[str]] = ("create",)
    expose_transitions: ClassVar[bool] = False


class ProcessStep:
    """What a handler gets: the event, the instance and the means to act.

    Attributes:
        envelope / payload: The event (``payload`` parsed when ``On.payload`` is set).
        key: The correlation key.
        instance: The process record (read model), or ``None`` before it starts.
        manager / actor: Run other operations as the process
            (``await step.manager.transition("invoices", id, "pay", actor=step.actor)``).
    """

    def __init__(
        self,
        runner: ProcessRunner,
        definition: ResourceDefinition,
        envelope: EventEnvelope,
        payload: Any,
        key: Any,
        instance: Any,
    ) -> None:
        self.runner = runner
        self.definition = definition
        self.envelope = envelope
        self.payload = payload
        self.key = key
        self.instance = instance

    @property
    def manager(self) -> ResourceManager:
        return self.runner.manager

    @property
    def actor(self) -> Any:
        return self.runner.actor

    @property
    def correlation_id(self) -> str | None:
        return self.envelope.correlation_id

    async def start(self, data: Any = None, *, tenant: str | None = None) -> Any:
        """Create the instance for this key (once: an existing one is returned)."""
        if self.instance is not None:
            return self.instance
        values = dict(data or {})
        values[self.definition.resource_cls.correlation] = self.key
        self.instance = await self.manager.create(
            self.definition.name, values, actor=self.actor, tenant=tenant
        )
        return self.instance

    async def transition(self, event: str, data: Any = None) -> Any:
        """Move the instance (its own state machine)."""
        if self.instance is None:
            raise ConfigurationError(
                f"{self.definition.name}: transition {event!r} before the process started",
                hint="Start it first (On(..., start=True) and step.start()).",
                doc=DOC,
            )
        instance_id = getattr(self.instance, self.definition.id_field)
        self.instance = await self.manager.transition(
            self.definition.name, instance_id, event, data, actor=self.actor
        )
        return self.instance

    def can(self, event: str) -> bool:
        """Whether the instance's state allows ``event`` now."""
        if self.instance is None or self.definition.machine is None:
            return False
        state = getattr(self.instance, self.definition.state_field or "", None)
        info = self.definition.machine.info
        value_of = {s.id: s.value if s.value is not None else s.id for s in info.states}
        return any(t.event == event and value_of[t.source] == state for t in info.transitions)


class ProcessRunner:
    """Dispatches events to every ``Process`` registered with ``manager``.

    Args:
        manager: The application's manager.
        actor: Who processes act as (default ``system_principal("processes")``).
    """

    def __init__(self, manager: ResourceManager, *, actor: Any = None) -> None:
        from resource_engine.authz import system_principal

        self.manager = manager
        self.actor = actor if actor is not None else system_principal("processes")
        self.processes = [d for d in manager.registry if issubclass(d.resource_cls, Process)]
        self._payloads: dict[int, TypeAdapter[Any]] = {}
        for d in self.processes:
            self._check(d)

    def _check(self, d: ResourceDefinition) -> None:
        cls = d.resource_cls
        correlation = getattr(cls, "correlation", None)
        where = f"process {d.name}"
        if not correlation:
            raise ConfigurationError(f"{where}: declare correlation", doc=DOC)
        create = d.schemas.get("create")
        read = d.schemas.get("read")
        if not d.enabled("create") or create is None or correlation not in create.model_fields:
            raise ConfigurationError(
                f"{where}: create must be enabled with {correlation!r} in its schema",
                hint="step.start() creates the instance with the correlation key.",
                doc=DOC,
            )
        if read is None or correlation not in read.model_fields or not d.enabled("list"):
            raise ConfigurationError(
                f"{where}: list must be enabled with {correlation!r} in the read schema",
                doc=DOC,
            )
        if not cls.reacts:
            raise ConfigurationError(f"{where}: reacts lists no events", doc=DOC)
        for on in cls.reacts:
            if not isinstance(on, On):
                raise ConfigurationError(f"{where}: reacts must hold On(...), not {on!r}")
            if on.payload is not None:
                self._payloads[id(on)] = TypeAdapter(on.payload)

    def reactions(self) -> list[tuple[ResourceDefinition, On]]:
        return [(d, on) for d in self.processes for on in d.resource_cls.reacts]

    def router(self, name: str = "processes") -> EventRouter:
        """An ``EventRouter`` with one handler per event type; events no process
        listens to are acknowledged."""
        router = EventRouter(
            ignore_unknown=True,
            name=name,
            description="Process managers: " + ", ".join(d.name for d in self.processes),
        )
        by_type: dict[str, list[tuple[ResourceDefinition, On]]] = {}
        for d, on in self.reactions():
            by_type.setdefault(on.event_type, []).append((d, on))
        for event_type, reactions in by_type.items():
            router.register(event_type, self._dispatcher(reactions))
        return router

    def _dispatcher(
        self, reactions: list[tuple[ResourceDefinition, On]]
    ) -> Callable[[EventEnvelope], Awaitable[None]]:
        async def dispatch(envelope: EventEnvelope) -> None:
            for definition, on in reactions:
                if on.matches(envelope):
                    await self._react(definition, on, envelope)

        return dispatch

    async def _react(self, definition: ResourceDefinition, on: On, envelope: EventEnvelope) -> None:
        key = on.key_of(envelope)
        if key is None:
            logger.warning(
                "%s: no correlation key at %r in %s", definition.name, on.key, envelope.type
            )
            return
        payload: Any = envelope.payload
        adapter = self._payloads.get(id(on))
        if adapter is not None:
            from pydantic import ValidationError

            from resource_engine.errors import MessageRejected

            try:
                payload = adapter.validate_python(dict(envelope.payload))
            except ValidationError as exc:
                raise MessageRejected(
                    f"Invalid {envelope.type} payload for process {definition.name}",
                    details=exc.errors(include_url=False, include_context=False),
                ) from exc
        instance = await self.instance(definition, key)
        if instance is None and not on.start:
            logger.debug("%s: no instance for %r; %s ignored", definition.name, key, envelope.type)
            return
        step = ProcessStep(self, definition, envelope, payload, key, instance)
        result = on.handler(step)
        if inspect.isawaitable(result):
            await result

    async def instance(self, definition: ResourceDefinition, key: Any) -> Any:
        """The process record for ``key``, or ``None``."""
        correlation = definition.resource_cls.correlation
        page = await self.manager.list(
            definition.name, ListQuery(limit=1), actor=self.actor, _within={correlation: key}
        )
        return page.items[0] if page.items else None


class ProcessFeed:
    """Feeds the outbox to the processes, for services without a broker of
    their own (a ``JobRunner`` job, like ``OutboxRelay``)::

        runner.add("processes", ProcessFeed(uow_factory, processor), every=1, single=True)

    Each run reads a batch of pending outbox records, runs each through
    ``processor`` (``MessageProcessor`` with the processes' router and
    ``manager=``: inbox and effects in one transaction, exactly once), then
    marks them published, or failed (retried on later runs up to
    ``max_attempts``). Records are processed outside the transaction that
    read them, so a process's own writes never compete with the outbox's.
    Permanent failures are logged and settled.

    With ``then=`` (a ``MessagePublisher``) the batch also goes to a broker, so
    one job empties the outbox for both; use it instead of ``OutboxRelay``.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        processor: MessageProcessor,
        *,
        then: Any = None,
        batch_size: int = 100,
        max_attempts: int = 10,
    ) -> None:
        self.uow_factory = uow_factory
        self.processor = processor
        self.then = then
        self.batch_size = batch_size
        self.max_attempts = max_attempts

    async def run_once(self) -> int:
        """Handle one batch; returns how many records were settled."""
        from resource_engine.messaging.processing import Outcome

        async with self.uow_factory() as uow:
            records = [
                r
                for r in await uow.outbox().pending(self.batch_size)
                if r.attempts < self.max_attempts
            ]
        if not records:
            return 0
        failed: dict[str, str] = {}
        if self.then is not None:
            try:
                await self.then.publish([r.envelope for r in records])
            except PublishFailed as exc:
                failed.update(dict.fromkeys(exc.failed_ids, exc.message))
            except Exception as exc:
                failed.update(dict.fromkeys((r.envelope.id for r in records), repr(exc)))
        for record in records:
            envelope = record.envelope
            if envelope.id in failed:
                continue
            result = await self.processor.process(
                ReceivedMessage(
                    envelope, delivery_count=record.attempts + 1, message_id=envelope.id
                )
            )
            if result.outcome is Outcome.RETRY:
                failed[envelope.id] = result.error or "retry"
            elif result.outcome is Outcome.DEAD_LETTER:  # reported by the processor
                logger.debug("Process message %s settled as dead letter", envelope.id)
        settled = [r.envelope.id for r in records if r.envelope.id not in failed]
        async with self.uow_factory() as uow:
            outbox = uow.outbox()
            if settled:
                await outbox.mark_published(settled)
            for envelope_id, error in failed.items():
                await outbox.mark_failed([envelope_id], error)
            await uow.commit()
        return len(settled)


__all__ = ["On", "Process", "ProcessFeed", "ProcessRunner", "ProcessStep"]
