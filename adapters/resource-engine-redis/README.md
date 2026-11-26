# resource-engine-redis

Redis and Valkey adapters for Resource Engine, on `redis-py` (asyncio):

- `RedisRateLimitStore`: rate-limit counters shared by every instance
  (`RateLimiter`, see [rate limits](../../docs/rate-limits.md)), one atomic
  script call per check, cluster-safe keys. If the store can't be reached it
  lets requests through by default (`fail_open`).
- `RedisCache`: the manager's record cache, shared by every instance
  ([caching](../../docs/caching.md)); entries pickled and signed with HMAC.

Tested against `valkey/valkey:8-alpine` (see `compose.emulators.yaml`, port 6380).
See the [adapter catalogue](../../docs/adapters.md). Part of [Resource Engine](../../README.md).
