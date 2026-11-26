"""Rate limiting: how many requests a caller may make per time window.

```python
limiter = SlidingWindowLimiter(
    InMemoryRateLimitStore(),            # one instance; RedisRateLimitStore to share
    default=Limit(600, per=60),          # per authenticated caller (principal or API key)
    anonymous=Limit(60, per=60),         # per client IP, without credentials
    failed_auth=Limit(10, per=300),      # failed authentications per IP (brute force)
    actions={"invoices:create": Limit(20, per=60), "*:action:*": Limit(30, per=60)},
)
ResourceManager(..., rate_limiter=limiter)                       # per action, every adapter
bearer_actor(authenticator, rate_limiter=limiter)                # per caller, HTTP
```

Counting uses a sliding window: the current fixed window's hits plus the
previous window's, weighted by how much of it still overlaps. It's accurate
to a few percent, costs one round trip per check, and never resets in a
burst at a window boundary. Refused requests count too, so a client that keeps
hammering stays refused. ``RateLimiter`` is the port
(``resource_engine.ports.ratelimit``): implement it to delegate elsewhere.
See docs/rate-limits.md.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from resource_engine.errors import RateLimited
from resource_engine.ports.ratelimit import Decision, RateLimiter


@dataclass(frozen=True)
class Limit:
    """At most ``requests`` per ``per`` seconds (or a ``timedelta``)."""

    requests: int
    per: float | timedelta = 60

    @property
    def window(self) -> float:
        return self.per.total_seconds() if isinstance(self.per, timedelta) else float(self.per)

    def __post_init__(self) -> None:
        if self.requests < 1 or self.window <= 0:
            raise ValueError("A limit needs requests >= 1 and a positive window")


class RateLimitStore(Protocol):
    """Counters per key and window (shared between instances to limit globally)."""

    async def hit(self, key: str, window: int, ttl: int, amount: int = 1) -> tuple[int, int]:
        """Add ``amount`` (0 reads) to ``key``'s counter for ``window`` (an index)
        and return it with the counter of ``window - 1``. Counters expire after
        ``ttl`` seconds."""
        ...


class InMemoryRateLimitStore:
    """Counters in this process (one instance, development and tests)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}
        self._expiry: dict[tuple[str, int], float] = {}

    async def hit(self, key: str, window: int, ttl: int, amount: int = 1) -> tuple[int, int]:
        now = time.monotonic()
        if len(self._expiry) > 10_000:  # forget expired windows now and then
            for k in [k for k, at in self._expiry.items() if at <= now]:
                self._counts.pop(k, None)
                self._expiry.pop(k, None)
        current = self._counts.get((key, window), 0) + amount
        if amount:
            self._counts[(key, window)] = current
            self._expiry.setdefault((key, window), now + ttl)
        return current, self._counts.get((key, window - 1), 0)


@dataclass
class SlidingWindowLimiter:
    """The built-in ``RateLimiter``: limits per caller, address and action, with
    sliding-window counters kept in a ``RateLimitStore``.

    Args:
        store: Where counters live.
        default: Per authenticated caller, for every request.
        anonymous: Per client IP, for requests without credentials.
        failed_auth: Failed authentications per client IP. Once reached, the
            IP's credentials are not even checked until the window frees up.
        actions: Per caller and engine action (``invoices:create``, patterns
            like ``invoices:*`` or ``*:action:*``), checked by the manager for
            every adapter. Each matching pattern is a limit of its own.
        exempt: Callers never limited (``lambda principal: principal.has_role(...)``).
            The engine's own system principals (jobs, consumers) always are.
        prefix: Key prefix in the store.
    """

    store: RateLimitStore
    default: Limit | None = None
    anonymous: Limit | None = None
    failed_auth: Limit | None = None
    actions: Mapping[str, Limit] = field(default_factory=dict)
    exempt: Callable[[Any], bool] | None = None
    prefix: str = "re:rl:"
    clock: Callable[[], float] = time.time

    async def check(self, key: str, limit: Limit, *, count: bool = True) -> Decision:
        """Count one request for ``key`` (``count=False`` only looks) and decide."""
        window = limit.window
        now = self.clock()
        index = int(now // window)
        elapsed = now - index * window
        current, previous = await self.store.hit(
            f"{self.prefix}{key}:{int(window)}",
            index,
            ttl=int(math.ceil(window * 2)),
            amount=1 if count else 0,
        )
        used = previous * (1 - elapsed / window) + current
        # looking: the next request would add one
        allowed = used <= limit.requests if count else used + 1 <= limit.requests
        reset = int(math.ceil(window - elapsed))
        retry = 0
        if not allowed:
            # until the previous window's weighted share has dropped enough
            excess = used - limit.requests + (0 if count else 1)
            retry = int(math.ceil(min(excess / previous * window, reset))) if previous else reset
            retry = max(retry, 1)
        return Decision(
            allowed=allowed,
            limit=limit.requests,
            remaining=max(0, int(limit.requests - used)),
            reset=reset,
            retry_after=retry,
            key=key,
        )

    async def enforce(self, key: str, limit: Limit, *, what: str, count: bool = True) -> Decision:
        decision = await self.check(key, limit, count=count)
        if not decision.allowed:
            raise RateLimited(
                f"Too many {what}: try again in {decision.retry_after} s",
                hint="Slow down; Retry-After says when to retry.",
                details={
                    "limit": decision.limit,
                    "window": int(limit.window),
                    "retry_after": decision.retry_after,
                    "scope": what,
                },
            )
        return decision

    def is_exempt(self, actor: Any) -> bool:
        from resource_engine.authz.evaluator import SystemPrincipal

        if isinstance(actor, SystemPrincipal):
            return True
        return bool(self.exempt and actor is not None and self.exempt(actor))

    async def caller(self, actor: Any) -> Decision | None:
        """The ``default`` limit of an authenticated caller."""
        if self.default is None or actor is None or self.is_exempt(actor):
            return None
        return await self.enforce(f"caller:{_id(actor)}", self.default, what="requests")

    async def client(self, address: str | None) -> Decision | None:
        """The ``anonymous`` limit of a client without credentials."""
        if self.anonymous is None:
            return None
        return await self.enforce(f"ip:{address or 'unknown'}", self.anonymous, what="requests")

    async def before_auth(self, address: str | None) -> None:
        """Refuse an address that already failed authentication too often."""
        if self.failed_auth is not None:
            await self.enforce(
                f"auth:{address or 'unknown'}",
                self.failed_auth,
                what="failed authentications",
                count=False,
            )

    async def failed(self, address: str | None) -> None:
        """Count one failed authentication of ``address``."""
        if self.failed_auth is not None:
            await self.check(f"auth:{address or 'unknown'}", self.failed_auth)

    async def action(self, actor: Any, action: str) -> None:
        """Every ``actions`` limit matching ``action``, for this caller."""
        if not self.actions or self.is_exempt(actor):
            return
        from resource_engine.authz.catalog import action_matches

        for pattern, limit in self.actions.items():
            if action_matches(pattern, action):
                await self.enforce(f"action:{pattern}:{_id(actor)}", limit, what=action)


def _id(actor: Any) -> str:
    return str(getattr(actor, "id", None) or "anonymous")


__all__ = [
    "Decision",
    "InMemoryRateLimitStore",
    "Limit",
    "RateLimitStore",
    "RateLimiter",
    "SlidingWindowLimiter",
]
