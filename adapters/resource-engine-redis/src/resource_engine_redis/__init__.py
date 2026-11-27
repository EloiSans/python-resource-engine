"""Redis (and Valkey) adapters for Resource Engine.

- ``RedisRateLimitStore``: rate-limit counters shared by every instance, one
  atomic script call per check.
- ``RedisCache``: the manager's record cache shared by every instance, so a
  write on one instance is forgotten on all of them.

```python
from redis.asyncio import Redis
from resource_engine.ratelimit import Limit, SlidingWindowLimiter
from resource_engine_redis import RedisRateLimitStore

limiter = SlidingWindowLimiter(RedisRateLimitStore(Redis.from_url(os.environ["REDIS_URL"])),
                      default=Limit(600, per=60), failed_auth=Limit(10, per=300))
```
"""

from __future__ import annotations

from typing import Any

from resource_engine.errors import StorageFailed

# INCRBY the current window (setting its expiry on first use), read the
# previous one; both keys share a hash tag, so this also works on a cluster.
_HIT = """
local amount = tonumber(ARGV[2])
local current
if amount > 0 then
  current = redis.call('INCRBY', KEYS[1], amount)
  if current == amount then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
else
  current = tonumber(redis.call('GET', KEYS[1]) or '0')
end
local previous = tonumber(redis.call('GET', KEYS[2]) or '0')
return {current, previous}
"""


class RedisRateLimitStore:
    """A ``RateLimitStore`` on Redis or Valkey (``redis.asyncio.Redis``).

    Args:
        client: An async Redis client (``Redis.from_url(...)``).
        fail_open: When Redis cannot be reached, let requests through (and
            log) instead of failing them; a limiter outage should not take the
            API down. ``False`` raises ``StorageFailed`` (a 502).
    """

    def __init__(self, client: Any, *, fail_open: bool = True) -> None:
        self.client = client
        self.fail_open = fail_open
        self._script = client.register_script(_HIT)

    async def hit(self, key: str, window: int, ttl: int, amount: int = 1) -> tuple[int, int]:
        tag = "{" + key + "}"
        try:
            current, previous = await self._script(
                keys=[f"{tag}:{window}", f"{tag}:{window - 1}"], args=[ttl, amount]
            )
        except Exception as exc:
            if self.fail_open:
                import logging

                logging.getLogger("resource_engine.ratelimit").warning(
                    "Rate limit store unavailable, letting the request through: %s", exc
                )
                return 0, 0
            raise StorageFailed(f"Rate limit store unavailable: {exc}") from exc
        return int(current), int(previous)


class RedisCache:
    """A ``Cache`` on Redis or Valkey.

    Values are pickled and signed with ``secret`` (HMAC-SHA256): an entry that
    was not written with the same secret is ignored, never unpickled, so a
    compromised cache cannot run code in the application.

    Args:
        client: An async Redis client.
        secret: Signing key (bytes or str), the same on every instance.
        prefix: Key prefix (several apps on one Redis).
    """

    def __init__(self, client: Any, *, secret: bytes | str, prefix: str = "") -> None:
        if not secret:
            raise ValueError("RedisCache needs a secret to sign its entries")
        self.client = client
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.prefix = prefix

    def _sign(self, payload: bytes) -> bytes:
        import hashlib
        import hmac

        return hmac.new(self.secret, payload, hashlib.sha256).digest()

    async def get(self, key: str) -> Any | None:
        import hmac
        import pickle

        raw = await self.client.get(self.prefix + key)
        if raw is None or len(raw) < 32:
            return None
        signature, payload = raw[:32], raw[32:]
        if not hmac.compare_digest(signature, self._sign(payload)):
            return None
        return pickle.loads(payload)  # noqa: S301 - signed by this application

    async def set(self, key: str, value: Any, ttl: float) -> None:
        import pickle

        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        await self.client.set(self.prefix + key, self._sign(payload) + payload, px=int(ttl * 1000))

    async def delete(self, *keys: str) -> None:
        if keys:
            await self.client.delete(*(self.prefix + k for k in keys))

    async def add(self, key: str, value: Any, ttl: float) -> bool:
        """``SET NX``: stored only when absent (one winner across instances)."""
        import pickle

        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        stored = await self.client.set(
            self.prefix + key, self._sign(payload) + payload, px=int(ttl * 1000), nx=True
        )
        return bool(stored)


__all__ = ["RedisCache", "RedisRateLimitStore"]
