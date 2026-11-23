"""Bridge between resources and ``python-statemachine``.

The state machine class lives in the module's domain layer and knows nothing about
sessions or transactions. The engine binds a fresh machine to the entity for each
operation, fires the event and then persists the entity through the repository.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from statemachine import StateMachine
from statemachine.exceptions import TransitionNotAllowed as _SMTransitionNotAllowed

from resource_engine.errors import TransitionNotAllowed, UnknownTransition


@dataclass(frozen=True)
class StateInfo:
    id: str
    name: str
    value: Any
    initial: bool
    final: bool


@dataclass(frozen=True)
class TransitionInfo:
    event: str
    source: str
    target: str


@dataclass(frozen=True)
class MachineInfo:
    states: tuple[StateInfo, ...]
    transitions: tuple[TransitionInfo, ...]

    @property
    def events(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for t in self.transitions:
            seen.setdefault(t.event, None)
        return tuple(seen)

    @property
    def initial(self) -> StateInfo | None:
        return next((s for s in self.states if s.initial), None)


def describe_machine(machine_cls: type[StateMachine]) -> MachineInfo:
    """Static description of a machine: states and transitions, in declaration order."""
    states = tuple(
        StateInfo(id=s.id, name=s.name, value=s.value, initial=s.initial, final=s.final)
        for s in machine_cls.states
    )
    transitions: list[TransitionInfo] = []
    for state in machine_cls.states:
        for t in state.transitions:
            for event in str(t.event).split():
                transitions.append(
                    TransitionInfo(event=event, source=t.source.id, target=t.target.id)
                )
    return MachineInfo(states=states, transitions=tuple(transitions))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class MachineRunner:
    """Executes a resource's state machine against one entity."""

    def __init__(self, machine_cls: type[StateMachine], state_field: str, resource: str) -> None:
        self.machine_cls = machine_cls
        self.state_field = state_field
        self.resource = resource
        self.info = describe_machine(machine_cls)

    def _bind(self, entity: Any) -> StateMachine:
        return self.machine_cls(model=entity, state_field=self.state_field)

    def current(self, entity: Any) -> Any:
        return getattr(entity, self.state_field, None)

    async def initialize(self, entity: Any) -> None:
        """Set the initial state on a new entity if it has none."""
        if self.current(entity) is not None:
            return
        sm = self._bind(entity)
        if self.current(entity) is None:  # async engines activate lazily
            await _maybe_await(sm.activate_initial_state())

    def allowed_events(self, entity: Any) -> list[str]:
        return [e.id for e in self._bind(entity).allowed_events]

    async def fire(self, entity: Any, event: str, payload: dict[str, Any]) -> tuple[Any, Any]:
        """Fire ``event`` on ``entity``. Returns ``(previous_state, new_state)``."""
        if event not in self.info.events:
            raise UnknownTransition(
                f"Resource {self.resource!r} has no transition {event!r}",
                hint=f"Available transitions: {', '.join(self.info.events) or '(none)'}",
            )
        previous = self.current(entity)
        sm = self._bind(entity)
        try:
            await _maybe_await(sm.send(event, **payload))
        except _SMTransitionNotAllowed as exc:
            allowed = [e.id for e in sm.allowed_events]
            raise TransitionNotAllowed(
                f"Transition {event!r} is not allowed for {self.resource!r} in state {previous!r}",
                hint=(
                    f"Allowed transitions from {previous!r}: {', '.join(allowed) or '(none)'}. "
                    "If the transition has guard conditions, they returned False."
                ),
                details={"state": previous, "allowed": allowed},
            ) from exc
        return previous, self.current(entity)
