"""Permissions and checks of the explorer: the live events, the worker's jobs
and publishing events by hand (docs/explorer.md).

These actions belong to no resource. ``register_explorer`` adds them to the
authorization catalogue, so the policy editor offers them like any other:

- ``events:read``: every committed event, including those about no record
  (others see only events about records they can read).
- ``jobs:read`` and ``jobs:run``: the worker's jobs, their runs, "run now".
- ``events:publish:<Event>``: publish that event by hand. It is *exact*: no
  wildcard grants it (``*`` and ``events:*`` don't), only a role or policy
  naming it.

The explorer's endpoints check them with ``require``. Without an authorizer
every one of them is refused, unless the app opens the explorer explicitly
for local development (``open=True``).
"""

from __future__ import annotations

from typing import Any

from resource_engine.authz.catalog import ActionInfo
from resource_engine.errors import PermissionDenied

RESOURCE = "explorer"

EVENTS_READ = ActionInfo(
    "events:read",
    resource="events",
    kind="engine",
    description="See every committed event live, including those about no record",
    wildcards=False,
)
JOBS_READ = ActionInfo(
    "jobs:read",
    resource="jobs",
    kind="engine",
    description="See the worker's jobs, their schedules and recent runs",
    wildcards=False,
)
JOBS_RUN = ActionInfo(
    "jobs:run",
    resource="jobs",
    kind="engine",
    description="Ask the worker to run a job now",
    wildcards=False,
)


def publish_action(event_type: str) -> ActionInfo:
    """``events:publish:<event_type>``: granted only by name."""
    return ActionInfo(
        f"events:publish:{event_type}",
        resource="events",
        kind="publish",
        qualifier=event_type,
        description=f"Publish {event_type} by hand from the explorer",
        wildcards=False,
        exact=True,
    )


def register_explorer(
    registry: Any, *, events: bool = True, jobs: bool = True, publish: bool = True
) -> None:
    """Add the explorer's actions to the registry's catalogue (before the
    ``ResourceManager`` is built)."""
    actions: list[ActionInfo] = []
    if events:
        actions.append(EVENTS_READ)
    if jobs:
        actions += [JOBS_READ, JOBS_RUN]
    if publish:
        actions += [publish_action(event.__name__) for event in registry.event_types()]
    registry.register_actions(*actions)


async def allowed(manager: Any, actor: Any, action: str, *, open: bool = False) -> bool:
    """Whether ``actor`` holds ``action``. Without an authorizer only an
    ``open`` explorer allows it."""
    authorizer = manager.authorizer
    if authorizer is None:
        return open
    return (await authorizer.check(actor, action)).allowed is True


async def require(
    manager: Any, actor: Any, action: str, *, open: bool = False, audit: bool = True
) -> None:
    """Raise ``PermissionDenied`` (and audit the refusal) unless ``actor`` holds
    ``action``."""
    if await allowed(manager, actor, action, open=open):
        return
    if audit:
        await manager.audit_event(actor, action, "denied")
    raise PermissionDenied(
        f"Not allowed: {action}",
        details={"action": action},
        hint=None
        if manager.authorizer is not None
        else "The explorer has no authorizer: pass open=True only for local development.",
    )


__all__ = [
    "EVENTS_READ",
    "JOBS_READ",
    "JOBS_RUN",
    "RESOURCE",
    "allowed",
    "publish_action",
    "register_explorer",
    "require",
]
