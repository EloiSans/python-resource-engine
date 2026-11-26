"""Timed transitions: deadlines declared on a resource, run by the worker.

```python
class InvoiceResource(Resource):
    state_since_field = "status_changed_at"  # stamped by the engine on every state change
    timers = [
        Timer("reject", after=timedelta(days=30), data={"reason": "No answer in 30 days"}),
    ]
```

A record is due when it has been in one of the timer's states (by default the
event's source states) for ``after``, counted from ``since`` (by default the
resource's ``state_since_field``). ``TimerJob`` finds due records and runs the
transition through ``ResourceManager.transition`` as the system principal, so
guards, hooks, the audit trail, events and the record's history apply as for
any transition. See docs/timers.md.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from resource_engine.errors import ConfigurationError, EntityNotFound, TransitionNotAllowed

if TYPE_CHECKING:
    from resource_engine.application.manager import ResourceManager
    from resource_engine.application.resource import ResourceDefinition

logger = logging.getLogger("resource_engine.timers")
DOC = "docs/timers.md"


@dataclass(frozen=True)
class Timer:
    """Run ``event`` once a record has waited ``after`` in one of ``states``.

    Args:
        event: The transition to run.
        after: How long the record waits first.
        since: Datetime field the wait is counted from (default: the resource's
            ``state_since_field``, the moment it entered its current state).
        states: States the record must be in (default: the event's sources).
        data: Input of the transition, validated against its schema.
        description: What the deadline means, for the generated docs.
    """

    event: str
    after: timedelta
    since: str | None = None
    states: Sequence[str] | None = None
    data: Mapping[str, Any] | None = None
    description: str | None = None


@dataclass(frozen=True)
class TimerDefinition:
    """A validated timer: ``states`` are the stored state values."""

    resource: str
    event: str
    after: timedelta
    since: str
    states: tuple[Any, ...]
    data: Mapping[str, Any] | None
    description: str | None


def normalize_timers(
    owner: str,
    name: str,
    timers: Sequence[Timer],
    *,
    machine: Any,
    state_since_field: str | None,
    transition_schemas: Mapping[str, Any],
    known_fields: set[str],
) -> tuple[TimerDefinition, ...]:
    out: list[TimerDefinition] = []
    if not timers:
        return ()
    if machine is None:
        raise ConfigurationError(f"{owner}: timers need a state machine", doc=DOC)
    info = machine.info
    value_of = {s.id: s.value if s.value is not None else s.id for s in info.states}
    values = set(value_of.values())
    for timer in timers:
        if not isinstance(timer, Timer):
            raise ConfigurationError(
                f"{owner}: timers must be Timer objects, not {timer!r}", doc=DOC
            )
        where = f"{owner} timer {timer.event!r}"
        sources = {value_of[t.source] for t in info.transitions if t.event == timer.event}
        if not sources:
            raise ConfigurationError(
                f"{where}: the state machine has no transition {timer.event!r}",
                hint=f"Transitions: {', '.join(info.events)}",
                doc=DOC,
            )
        if timer.after <= timedelta(0):
            raise ConfigurationError(f"{where}: after must be positive", doc=DOC)
        since = timer.since or state_since_field
        if since is None:
            raise ConfigurationError(
                f"{where}: no since field",
                hint="Set state_since_field on the resource (the engine stamps it on every "
                "state change) or pass since= a datetime field.",
                doc=DOC,
            )
        if since not in known_fields:
            raise ConfigurationError(
                f"{where}: since field {since!r} is not part of the read or write schemas",
                doc=DOC,
            )
        states = tuple(timer.states) if timer.states else tuple(sorted(sources, key=str))
        unknown = [s for s in states if s not in values]
        not_sources = [s for s in states if s in values and s not in sources]
        if unknown or not_sources:
            raise ConfigurationError(
                f"{where}: "
                + (f"unknown states {unknown}" if unknown else "")
                + (f"; {timer.event!r} cannot run from {not_sources}" if not_sources else ""),
                hint=f"{timer.event!r} runs from: {sorted(map(str, sources))}",
                doc=DOC,
            )
        schema = transition_schemas.get(timer.event)
        if schema is not None:
            try:
                schema.model_validate(dict(timer.data or {}))
            except Exception as exc:
                raise ConfigurationError(
                    f"{where}: data does not match the {schema.__name__} schema: {exc}",
                    doc=DOC,
                ) from exc
        elif timer.data:
            raise ConfigurationError(f"{where}: {timer.event!r} takes no input data", doc=DOC)
        out.append(
            TimerDefinition(
                resource=name,
                event=timer.event,
                after=timer.after,
                since=since,
                states=states,
                data=dict(timer.data) if timer.data else None,
                description=timer.description,
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class TimerRun:
    """What one ``TimerJob.run_once`` did."""

    fired: int = 0
    skipped: int = 0
    failed: int = 0
    due: dict[str, int] = dataclasses.field(default_factory=dict)


class TimerJob:
    """Runs the due timed transitions of every resource (a ``JobRunner`` job).

    Args:
        manager: The application's manager.
        resources: Only these resources (default: every resource with timers).
        batch: Records handled per timer and run; the rest wait for the next run.
        actor: Who runs the transitions (default ``system_principal("timers")``).
        clock: ``() -> datetime`` (UTC), for tests.

    Run it with ``single=True`` so one worker fires each deadline. A record
    whose state changed meanwhile is skipped; other failures are logged and
    retried on the next run.
    """

    def __init__(
        self,
        manager: ResourceManager,
        *,
        resources: Sequence[str] | None = None,
        batch: int = 100,
        actor: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        from resource_engine.authz import system_principal

        self.manager = manager
        self.resources = resources
        self.batch = batch
        self.actor = actor if actor is not None else system_principal("timers")
        self.clock = clock or (lambda: datetime.now(UTC))

    def timers(self) -> list[tuple[ResourceDefinition, TimerDefinition]]:
        return [
            (d, t)
            for d in self.manager.registry
            if self.resources is None or d.name in self.resources
            for t in d.timers
        ]

    async def run_once(self) -> TimerRun:
        from resource_engine.application.query import ListQuery

        now = self.clock()
        fired = skipped = failed = 0
        due: dict[str, int] = {}
        for definition, timer in self.timers():
            condition = {
                "and": [
                    {"in": [{"attr": f"resource.{definition.state_field}"}, list(timer.states)]},
                    {"exists": {"attr": f"resource.{timer.since}"}},
                    {"le": [{"attr": f"resource.{timer.since}"}, now - timer.after]},
                ]
            }
            page = await self.manager.list(
                definition.name,
                ListQuery(limit=self.batch),
                actor=self.actor,
                _condition=condition,
                _unbounded=True,
            )
            key = f"{definition.name}.{timer.event}"
            due[key] = page.total
            for record in page.items:
                entity_id = getattr(record, definition.id_field)
                try:
                    await self.manager.transition(
                        definition.name,
                        entity_id,
                        timer.event,
                        dict(timer.data) if timer.data else None,
                        actor=self.actor,
                    )
                except (TransitionNotAllowed, EntityNotFound):
                    skipped += 1  # the record moved on since it was listed
                except Exception as exc:
                    from resource_engine.reporting import report_error

                    failed += 1
                    await report_error(
                        exc,
                        where="timer",
                        force=True,
                        timer=key,
                        resource=definition.name,
                        entity_id=entity_id,
                    )
                else:
                    fired += 1
        return TimerRun(fired, skipped, failed, due)

    async def __call__(self) -> TimerRun:
        return await self.run_once()


__all__ = ["Timer", "TimerDefinition", "TimerJob", "TimerRun", "normalize_timers"]
