"""Typed lifecycle hooks and their dispatcher."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from resource_engine.errors import ConfigurationError, ExternalEffectInTransaction

if TYPE_CHECKING:
    from resource_engine.application.context import OperationContext

type Hook = Callable[[OperationContext], Awaitable[None] | None]

_EXTERNAL_EFFECT_ATTR = "__resource_engine_external_effect__"


class HookPoint(StrEnum):
    """Points of the lifecycle where hooks can run. See docs/lifecycle.md for the order."""

    BEFORE_VALIDATE = "before_validate"
    AFTER_VALIDATE = "after_validate"
    BEFORE_CREATE = "before_create"
    AFTER_CREATE = "after_create"
    BEFORE_UPDATE = "before_update"
    AFTER_UPDATE = "after_update"
    BEFORE_DELETE = "before_delete"
    AFTER_DELETE = "after_delete"
    BEFORE_TRANSITION = "before_transition"
    AFTER_TRANSITION = "after_transition"
    BEFORE_ACTION = "before_action"
    AFTER_ACTION = "after_action"
    BEFORE_ATTACH = "before_attach"
    AFTER_ATTACH = "after_attach"
    BEFORE_DETACH = "before_detach"
    AFTER_DETACH = "after_detach"
    AFTER_COMMIT = "after_commit"

    @property
    def transactional(self) -> bool:
        """Whether hooks at this point run inside the unit of work."""
        return self not in (
            HookPoint.BEFORE_VALIDATE,
            HookPoint.AFTER_VALIDATE,
            HookPoint.AFTER_COMMIT,
        )


# Points that accept a ``:qualifier`` (transition event or action name).
QUALIFIABLE = {
    HookPoint.BEFORE_TRANSITION,
    HookPoint.AFTER_TRANSITION,
    HookPoint.BEFORE_ACTION,
    HookPoint.AFTER_ACTION,
    HookPoint.BEFORE_ATTACH,
    HookPoint.AFTER_ATTACH,
    HookPoint.BEFORE_DETACH,
    HookPoint.AFTER_DETACH,
}


def external_effect[F: Callable[..., Any]](fn: F) -> F:
    """Mark a hook as producing external effects (emails, HTTP calls, messages).

    Such hooks may only be registered at ``after_commit``; the engine refuses any
    other point at bootstrap with RE-E104.
    """
    setattr(fn, _EXTERNAL_EFFECT_ATTR, True)
    return fn


def has_external_effect(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, _EXTERNAL_EFFECT_ATTR, False))


@dataclass(frozen=True)
class HookKey:
    point: HookPoint
    qualifier: str | None = None

    def __str__(self) -> str:
        return f"{self.point.value}:{self.qualifier}" if self.qualifier else self.point.value

    @classmethod
    def parse(cls, raw: str | HookPoint) -> HookKey:
        if isinstance(raw, HookPoint):
            return cls(raw)
        name, _, qualifier = raw.partition(":")
        try:
            point = HookPoint(name)
        except ValueError:
            valid = ", ".join(p.value for p in HookPoint)
            raise ConfigurationError(
                f"Unknown hook point {raw!r}",
                hint=f"Use one of: {valid}",
                doc="docs/lifecycle.md#hook-points",
            ) from None
        if qualifier and point not in QUALIFIABLE:
            raise ConfigurationError(
                f"Hook point {name!r} does not accept a qualifier ({raw!r})",
                hint="Only *_transition and *_action points accept ':<name>'.",
                doc="docs/lifecycle.md#hook-points",
            )
        return cls(point, qualifier or None)


def _callable_name(fn: Callable[..., Any]) -> str:
    return getattr(fn, "__qualname__", None) or repr(fn)


def normalize_hooks(
    raw: Mapping[str | HookPoint, Hook | Sequence[Hook]], *, owner: str
) -> dict[HookKey, tuple[Hook, ...]]:
    """Validate a ``hooks = {...}`` declaration and return it keyed by HookKey."""
    result: dict[HookKey, tuple[Hook, ...]] = {}
    for raw_key, value in raw.items():
        key = HookKey.parse(raw_key)
        fns: Iterable[Hook] = (value,) if callable(value) else value
        fns = tuple(fns)
        for fn in fns:
            if not callable(fn):
                raise ConfigurationError(
                    f"Hook {fn!r} registered at {key} of {owner} is not callable"
                )
            if has_external_effect(fn) and key.point is not HookPoint.AFTER_COMMIT:
                raise ExternalEffectInTransaction(
                    f"The hook {_callable_name(fn)!r} registered at {str(key)!r} of {owner} "
                    "produces external effects.",
                    hint="Move it to 'after_commit' or record a domain event instead.",
                    doc="docs/lifecycle.md#external-effects",
                )
        result[key] = result.get(key, ()) + fns
    return result


class HookDispatcher:
    """Runs hooks for a lifecycle point: generic ones first, then qualified ones."""

    def __init__(self, hooks: Mapping[HookKey, Sequence[Hook]]) -> None:
        self._hooks = hooks

    def for_point(self, point: HookPoint, qualifier: str | None = None) -> list[Hook]:
        hooks = list(self._hooks.get(HookKey(point), ()))
        if qualifier:
            hooks.extend(self._hooks.get(HookKey(point, qualifier), ()))
        return hooks

    async def run(
        self, point: HookPoint, ctx: OperationContext, qualifier: str | None = None
    ) -> None:
        for hook in self.for_point(point, qualifier):
            try:
                result = hook(ctx)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                exc.add_note(
                    f"[resource-engine] raised by hook {_callable_name(hook)!r} at "
                    f"{point.value}{':' + qualifier if qualifier else ''} "
                    f"of resource {ctx.resource.name!r}"
                )
                raise
