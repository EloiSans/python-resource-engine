"""Rate limit port: decides whether a caller may make one more request.

Entry adapters and the manager call it at fixed points; what it counts and
where is the implementation's business. ``resource_engine.ratelimit`` ships
``SlidingWindowLimiter`` (counters in a ``RateLimitStore``: memory or Redis).
Implement the port to delegate to something else: a gateway's rate-limit
service (Envoy RLS), a SaaS limiter, quotas billed per customer.

Each method returns ``None`` or a ``Decision`` when the request may go on (the
decision feeds the ``RateLimit-*`` headers) and raises ``RateLimited``
(RE-E710) when it may not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Decision:
    """The outcome of one check, with what the RateLimit headers report."""

    allowed: bool
    limit: int
    remaining: int
    reset: int  # seconds until the window frees up
    retry_after: int  # seconds to wait when refused (0 when allowed)
    key: str = ""


class RateLimiter(Protocol):
    async def caller(self, actor: Any) -> Decision | None:
        """One request of an authenticated caller (HTTP, each MCP tool call)."""
        ...

    async def client(self, address: str | None) -> Decision | None:
        """One request without credentials, from ``address``."""
        ...

    async def before_auth(self, address: str | None) -> None:
        """Before checking credentials from ``address`` (refuse brute force)."""
        ...

    async def failed(self, address: str | None) -> None:
        """Credentials from ``address`` were wrong."""
        ...

    async def action(self, actor: Any, action: str) -> None:
        """The manager is about to run ``action`` (``invoices:create``) for ``actor``."""
        ...
