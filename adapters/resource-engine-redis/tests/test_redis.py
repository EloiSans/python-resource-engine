"""Redis adapters against Valkey: shared rate-limit counters and the signed cache."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from resource_engine.errors import StorageFailed
from resource_engine.ratelimit import Limit, SlidingWindowLimiter
from resource_engine_redis import RedisRateLimitStore


@pytest.mark.emulator("redis")
async def test_instances_share_the_counters(client):
    prefix = f"re:test:{uuid.uuid4().hex[:8]}:"
    limit = Limit(5, per=60)
    a = SlidingWindowLimiter(RedisRateLimitStore(client), prefix=prefix)
    b = SlidingWindowLimiter(RedisRateLimitStore(client), prefix=prefix)  # another instance
    results = await asyncio.gather(*(x.check("k", limit) for x in (a, b) * 4))
    assert sum(r.allowed for r in results) == 5  # atomic: never more than the limit
    peek = await a.check("k", limit, count=False)
    assert not peek.allowed
    [key] = [k async for k in client.scan_iter(f"{{{prefix}k:60}}:*")]
    assert 60 < await client.ttl(key) <= 120  # counters expire on their own


@pytest.mark.emulator("redis")
async def test_an_unreachable_store_fails_open_or_closed():
    from redis.asyncio import Redis

    down = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limit = Limit(1, per=60)
    open_ = SlidingWindowLimiter(RedisRateLimitStore(down))
    assert all([(await open_.check("k", limit)).allowed for _ in range(3)])
    closed = SlidingWindowLimiter(RedisRateLimitStore(down, fail_open=False))
    with pytest.raises(StorageFailed):
        await closed.check("k", limit)
    await down.aclose()


@pytest.mark.emulator("redis")
async def test_the_cache_is_shared_signed_and_expires(client):
    from resource_engine_redis import RedisCache

    prefix = f"re:test:{uuid.uuid4().hex[:8]}:"
    one = RedisCache(client, secret="s3cret", prefix=prefix)
    other = RedisCache(client, secret="s3cret", prefix=prefix)  # another instance
    await one.set("rec:customers:1", {"name": "ACME", "n": 1}, ttl=30)
    assert await other.get("rec:customers:1") == {"name": "ACME", "n": 1}
    assert 0 < await client.pttl(prefix + "rec:customers:1") <= 30_000
    forger = RedisCache(client, secret="guess", prefix=prefix)
    assert await forger.get("rec:customers:1") is None  # never unpickled
    await client.set(prefix + "evil", b"x" * 40)
    assert await one.get("evil") is None
    await other.delete("rec:customers:1")
    assert await one.get("rec:customers:1") is None
    with pytest.raises(ValueError):
        RedisCache(client, secret="")
