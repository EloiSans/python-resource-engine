"""How the ResourceManager delivers the events recorded during an operation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from resource_engine.domain.events import DomainEvent
from resource_engine.errors import OutboxNotSupported
from resource_engine.messaging.envelope import EventEnvelope, EventSerializer
from resource_engine.ports.event_publisher import EventPublisher
from resource_engine.ports.messaging import MessagePublisher, SupportsOutbox

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext
    from resource_engine.ports.unit_of_work import UnitOfWork

logger = logging.getLogger("resource_engine.messaging")


class EventDelivery(Protocol):
    """Strategy called by the manager right before and right after commit."""

    async def before_commit(self, ctx: OperationContext, uow: UnitOfWork) -> None: ...

    async def after_commit(self, ctx: OperationContext) -> None: ...


class AfterCommitDelivery:
    """Publish in-process after commit. Best effort: lost if the process dies."""

    def __init__(self, publisher: EventPublisher) -> None:
        self.publisher = publisher

    async def before_commit(self, ctx: OperationContext, uow: UnitOfWork) -> None:
        return None

    async def after_commit(self, ctx: OperationContext) -> None:
        if ctx.events:
            await self.publisher.publish(list(ctx.events))


class TransportEventPublisher:
    """``EventPublisher`` that serialises events and sends them to a broker."""

    def __init__(
        self, transport: MessagePublisher, serializer: EventSerializer | None = None
    ) -> None:
        self.transport = transport
        self.serializer = serializer or EventSerializer()

    async def publish(self, events: Sequence[DomainEvent]) -> None:
        await self.transport.publish(
            [envelope for e in events for envelope in self.serializer.to_envelopes(e)]
        )


class OutboxDelivery:
    """Transactional outbox: events are stored with the business change.

    Envelopes are written through ``uow.outbox()`` before commit, so they exist if
    and only if the operation committed. An ``OutboxRelay`` sends them later.

    ``live``: a ``MessagePublisher`` in this process (an ``EventHub``) that also
    gets the same envelopes right after the commit, for live subscribers (the
    explorer, GraphQL subscriptions). Best effort: a failure there is logged
    and changes nothing; the outbox stays the source of delivery.
    """

    def __init__(self, serializer: EventSerializer | None = None, *, live: Any = None) -> None:
        self.serializer = serializer or EventSerializer()
        self.live = live

    def envelopes(self, ctx: OperationContext) -> list[EventEnvelope]:
        return [
            envelope
            for e in ctx.events
            for envelope in self.serializer.to_envelopes(
                e, correlation_id=ctx.correlation_id, resource=ctx.resource.name
            )
        ]

    async def before_commit(self, ctx: OperationContext, uow: UnitOfWork) -> None:
        if not ctx.events:
            return
        if not isinstance(uow, SupportsOutbox):
            raise OutboxNotSupported(
                f"{type(uow).__name__} has no outbox()",
                hint="Use a unit of work with an outbox (SqlAlchemyUnitOfWork with "
                "outbox_table, InMemoryUnitOfWork) or AfterCommitDelivery.",
                doc="docs/messaging.md#outbox",
            )
        envelopes = self.envelopes(ctx)
        ctx.extras["outbox_envelopes"] = envelopes
        await uow.outbox().add(envelopes)

    async def after_commit(self, ctx: OperationContext) -> None:
        envelopes = ctx.extras.get("outbox_envelopes")
        if self.live is None or not envelopes:
            return
        try:
            await self.live.publish(envelopes)
        except Exception:
            logger.exception("Live subscribers did not get the committed events")
