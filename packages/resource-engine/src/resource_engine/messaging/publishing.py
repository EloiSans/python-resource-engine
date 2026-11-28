"""Publishing an event by hand: the explorer's PUB rows (docs/explorer.md#pub).

Testing a consumer often needs one particular event: an invoice approved
with an odd amount, an old schema version, a payment that never arrives.
``EventPublishing`` lets an authorized person write that event and send it
the way the application sends its own:

```python
publishing = EventPublishing(manager, consumers=[billing_router], allow_publish=True)
problems = await publishing.validate("InvoiceApproved", 2, payload, actor=user)  # dry run
envelope = await publishing.publish("InvoiceApproved", 2, payload, actor=user)
```

- **Which events:** those the application's resources declare
  (``Resource.events``), in each version they publish (``schema_version`` and
  ``previous_versions``). Engine events (``ResourceCreated``...) are not
  published by hand: they describe changes that must really happen.
- **Permission:** ``events:publish:<Event>``, *exact*: no wildcard grants it,
  only a role or policy naming it (``register_explorer`` adds the actions).
  Even then, publishing needs ``allow_publish=True``; a dry run doesn't.
- **Dry run:** the payload is validated against the event's own model for
  that version, then against each consumer's payload model for it (the
  ``EventRouter``s given as ``consumers``). Problems come back with the check,
  the field's path and the message. Nothing is written. Only the event's own
  problems stop a publication: one a consumer will refuse can still be sent,
  to test how it copes (retries, dead letters).
- **Publishing:** the envelope goes into the outbox in a transaction of its
  own, with an audit entry, exactly as an operation's events do; the relay
  sends it to the brokers. It never goes straight to a broker. The envelope
  is tagged ``origin=console`` and ``publishedby=<actor>``. Publishing the
  current version also publishes the older ones the event keeps, converted,
  like any other publication; an older version is published alone.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from resource_engine.errors import ConfigurationError, EntityNotFound, PermissionDenied
from resource_engine.explorer import publish_action, require
from resource_engine.messaging.envelope import EventEnvelope, EventSerializer, to_jsonable
from resource_engine.schema_examples import example


def publishable_events(registry: Any) -> list[type]:
    """The events people may publish by hand: those the application's
    resources declare, not the engine's own."""
    return [e for e in registry.event_types() if not e.__module__.startswith("resource_engine.")]


@dataclass(frozen=True)
class Problem:
    """Why a payload doesn't fit: ``check`` is ``event`` (its own model) or
    ``consumer:<name>``; ``path`` the field (``amount``, ``lines.0.qty``)."""

    check: str
    path: str
    message: str


@dataclass(frozen=True)
class PublishableVersion:
    version: str
    schema: dict[str, Any]
    example: dict[str, Any]
    deprecated: bool = False


@dataclass(frozen=True)
class PublishableEvent:
    type: str
    resource: str | None
    summary: str
    versions: tuple[PublishableVersion, ...] = field(default_factory=tuple)


def _fields_schema(model: Any) -> dict[str, Any]:
    """The JSON schema of an event's payload (its fields but ``occurred_at``)."""
    schema = TypeAdapter(model).json_schema()
    properties = dict(schema.get("properties", {}))
    properties.pop("occurred_at", None)
    required = [r for r in schema.get("required", []) if r != "occurred_at"]
    out = {**schema, "properties": properties}
    if required:
        out["required"] = required
    else:
        out.pop("required", None)
    return out


class EventPublishing:
    """Dry runs and publications of the application's events, by hand.

    Args:
        manager: The ``ResourceManager``: its registry, authorizer, outbox and audit.
        consumers: ``EventRouter``s whose payload models a dry run checks too.
        allow_publish: Publishing for real (a dry run is always possible).
        open: Without an authorizer, allow it anyway (local development only).
    """

    def __init__(
        self,
        manager: Any,
        *,
        consumers: Sequence[Any] = (),
        allow_publish: bool = False,
        open: bool = False,
    ) -> None:
        self.manager = manager
        self.consumers = list(consumers)
        self.allow_publish = allow_publish
        self.open = open
        self.serializer = EventSerializer()
        self.events = {e.__name__: e for e in publishable_events(manager.registry)}
        self.owners: dict[str, str] = {}
        for definition in manager.registry:
            for event in definition.resource_cls.events:
                self.owners.setdefault(event.__name__, definition.name)

    # ------------------------------------------------------------ catalogue

    def _event(self, event_type: str) -> type:
        event = self.events.get(event_type)
        if event is None:
            raise EntityNotFound(
                f"No event {event_type!r} can be published",
                hint=f"Publishable events: {', '.join(sorted(self.events)) or '(none)'}",
            )
        return event

    def _model(self, event_type: str, version: int | str | None) -> tuple[Any, int]:
        """The payload model of that version, and the version as a number."""
        event = self._event(event_type)
        current = int(getattr(event, "schema_version", 1))
        number = current if version in (None, "") else int(str(version).lstrip("v"))
        if number == current:
            return event, number
        older = getattr(event, "previous_versions", {}) or {}
        if number not in older:
            raise EntityNotFound(
                f"{event_type} has no version {number}",
                hint=f"Versions: {sorted([current, *older])}",
            )
        return older[number].schema, number

    def catalogue(self) -> list[PublishableEvent]:
        from resource_engine.domain.events import event_doc

        out = []
        for name, event in sorted(self.events.items()):
            current = int(getattr(event, "schema_version", 1))
            older = getattr(event, "previous_versions", {}) or {}
            versions = []
            for number in sorted([current, *older]):
                model, _ = self._model(name, number)
                schema = _fields_schema(model)
                deprecation = older[number].deprecation if number in older else event.deprecation
                versions.append(
                    PublishableVersion(
                        str(number),
                        schema,
                        example(schema, schema.get("$defs", {})) or {},
                        deprecation is not None,
                    )
                )
            doc = (event_doc(event) or "").strip().split("\n\n")[0].replace("\n", " ")
            out.append(PublishableEvent(name, self.owners.get(name), doc, tuple(versions)))
        return out

    # --------------------------------------------------------------- checks

    async def _require(self, actor: Any, event_type: str) -> None:
        self._event(event_type)  # an unknown type is "not found", not "not allowed"
        await require(self.manager, actor, publish_action(event_type).name, open=self.open)

    def problems(self, event_type: str, version: int | str | None, payload: Any) -> list[Problem]:
        """What doesn't fit in ``payload`` (no permission check, nothing written)."""
        model, number = self._model(event_type, version)
        if not isinstance(payload, dict):
            return [Problem("event", "", "The payload must be a JSON object")]
        out = _validate("event", model, payload)
        for router in self.consumers:
            wanted = router.schema_version(event_type)
            payload_type = router.payload_type(event_type)
            if payload_type is None or (wanted is not None and wanted != str(number)):
                continue
            name = f"consumer:{getattr(router, 'name', None) or type(router).__name__}"
            out += _validate(name, payload_type, payload)
        return out

    async def validate(
        self, event_type: str, version: int | str | None, payload: Any, *, actor: Any = None
    ) -> list[Problem]:
        """A dry run: the permission, then ``problems``."""
        await self._require(actor, event_type)
        return self.problems(event_type, version, payload)

    # ----------------------------------------------------------- publishing

    async def publish(
        self, event_type: str, version: int | str | None, payload: Any, *, actor: Any = None
    ) -> list[EventEnvelope]:
        """Validate and publish through the outbox, as ``actor``. Returns the
        envelopes written (the current version brings its older ones)."""
        await self._require(actor, event_type)
        action = publish_action(event_type).name
        if not self.allow_publish:
            await self.manager.audit_event(actor, action, "denied", details={"why": "disabled"})
            raise PermissionDenied(
                "Publishing events by hand is turned off",
                details={"action": action},
                hint="EventPublishing(manager, allow_publish=True) turns it on.",
            )
        # a consumer that can't read it is worth testing (dead letters): only the
        # event's own model blocks a publication
        problems = [p for p in self.problems(event_type, version, payload) if p.check == "event"]
        if problems:
            from resource_engine.errors import ValidationFailed

            raise ValidationFailed(
                f"The {event_type} payload doesn't fit",
                details=[dataclasses.asdict(p) for p in problems],
            )
        envelopes = self._envelopes(event_type, version, payload, actor)
        async with self.manager.transaction() as tx:
            outbox = getattr(tx.uow, "outbox", None)
            if outbox is None:
                raise ConfigurationError(
                    "Publishing events by hand needs a unit of work with an outbox",
                    hint="Events go out the way the application's own do: OutboxDelivery and "
                    "OutboxRelay.",
                    doc="docs/explorer.md#pub",
                )
            await outbox().add(envelopes)
            await self.manager.audit_event(
                actor,
                action,
                details={
                    "event": event_type,
                    "version": envelopes[0].schema_version,
                    "envelopes": [e.id for e in envelopes],
                    "payload": envelopes[0].payload,
                },
                uow=tx.uow,
            )
        live = getattr(self.manager.delivery, "live", None)
        if live is not None:  # the explorer's SUB rows see it at once, like any event
            with contextlib.suppress(Exception):  # best effort, as for OutboxDelivery
                await live.publish(envelopes)
        return envelopes

    def _envelopes(
        self, event_type: str, version: int | str | None, payload: dict[str, Any], actor: Any
    ) -> list[EventEnvelope]:
        from resource_engine.audit.recorder import actor_id

        model, number = self._model(event_type, version)
        who = actor if isinstance(actor, str) else actor_id(actor)
        tags = {"origin": "console", **({"publishedby": str(who)} if who else {})}
        resource = self.owners.get(event_type)
        event = self._event(event_type)
        if model is event:
            instance = TypeAdapter(event).validate_python(payload)
            envelopes = self.serializer.to_envelopes(instance, resource=resource)
        else:
            data = to_jsonable(TypeAdapter(model).dump_python(
                TypeAdapter(model).validate_python(payload), mode="json"
            ))  # fmt: skip
            data.pop("occurred_at", None)
            entity_id = data.get("entity_id")
            key = f"{resource}:{entity_id}" if resource and entity_id is not None else resource
            envelopes = [
                EventEnvelope(
                    type=event_type,
                    payload=data,
                    occurred_at=datetime.now(UTC),
                    resource=resource,
                    key=key,
                    schema_version=str(number),
                )
            ]
        return [dataclasses.replace(e, tags=tags) for e in envelopes]


def _validate(check: str, model: Any, payload: dict[str, Any]) -> list[Problem]:
    try:
        TypeAdapter(model).validate_python(payload)
    except ValidationError as exc:
        return [
            Problem(check, ".".join(str(p) for p in error["loc"]), error["msg"])
            for error in exc.errors()
        ]
    return []


__all__ = [
    "EventPublishing",
    "Problem",
    "PublishableEvent",
    "PublishableVersion",
    "publishable_events",
]
