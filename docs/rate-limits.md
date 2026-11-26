# Rate limits

Every API built on the engine can limit how many requests a caller makes.
Rate limiting is a port, `RateLimiter`. The built-in `SlidingWindowLimiter`
holds the rules, and a store holds its counters:

```python
from resource_engine.ratelimit import InMemoryRateLimitStore, Limit, SlidingWindowLimiter

limiter = SlidingWindowLimiter(
    InMemoryRateLimitStore(),  # one instance; RedisRateLimitStore to share counters
    default=Limit(600, per=60),  # per authenticated caller (user, service, API key)
    anonymous=Limit(60, per=60),  # per client address, without credentials
    failed_auth=Limit(20, per=300),  # failed authentications per address (brute force)
    actions={  # per caller and engine action, in every adapter
        "invoices:create": Limit(120, per=60),
        "*:action:render_summary": Limit(10, per=60),
    },
    exempt=lambda principal: principal.has_role("load-test"),
)

manager = ResourceManager(..., rate_limiter=limiter)  # actions
actor = bearer_actor(authenticator, rate_limiter=limiter)  # default, anonymous, failed_auth
mount_resources(app, registry, manager, actor_dependency=actor)
mount_graphql(app, registry, manager, actor_dependency=actor, rate_limiter=limiter)
ResourceMcpServer(registry, manager, rate_limiter=limiter)
```

## What is limited where

| Limit | Key | Checked by |
|---|---|---|
| `default` | the caller (`Principal.id`, `apikey:<id>`) | `bearer_actor` (REST, GraphQL over HTTP), each MCP tool call |
| `anonymous` | client address | `bearer_actor(required=False)` without credentials |
| `failed_auth` | client address | `bearer_actor` and the GraphQL WebSocket: failures count, and once the limit is reached the address's credentials aren't even checked |
| `actions` | caller and pattern | the manager, before the permission check, for every adapter (REST, GraphQL, MCP, processes run as a user) |

Action patterns use the permission syntax (`invoices:*`, `*:delete`). Each
matching pattern is a limit of its own. The engine's system principals (jobs,
timers, consumers) and `exempt` callers are never limited.

## Responses

A refused request is RE-E710 `RateLimited`: HTTP 429 with `Retry-After`,
`RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset`, and
`details.retry_after` and `details.scope` in the body. GraphQL returns the code
in `extensions`, MCP returns it as a tool error, and the WebSocket closes with
4429. Allowed REST requests carry the `RateLimit-*` headers of the caller's
`default` limit.

Front ends and clients wait `Retry-After` seconds before retrying, and back
off further when retries keep failing.

## How it counts

A sliding window: the current fixed window's count plus the previous window's,
weighted by how much of it still overlaps. That's one store round trip per
check, accurate to a few percent, with no burst at window boundaries. Refused
requests count too, so a client that keeps hammering stays refused.

## Stores

- `InMemoryRateLimitStore`: one process. Each instance counts on its own, so
  with N instances a caller gets up to N times the limit. Good for one
  instance, development and tests.
- `RedisRateLimitStore` (`resource-engine-redis`): counters in Redis or Valkey,
  shared by every instance, through one atomic script per check with
  cluster-safe keys. If the store can't be reached, requests go through and a
  warning is logged (`fail_open=True`). With `fail_open=False` they fail with
  a 502.

```python
from redis.asyncio import Redis
from resource_engine_redis import RedisRateLimitStore

store = RedisRateLimitStore(Redis.from_url(os.environ["REDIS_URL"]))
```

## Client addresses

The address is `request.client.host`. Behind a load balancer or proxy, run
uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy addresses>` so it's
the real client's. Otherwise every client shares the proxy's address. The
engine doesn't read `X-Forwarded-For` itself, because anyone can send it.

## Your own limiter

`RateLimiter` (`resource_engine.ports.ratelimit`) is the port the adapters and
the manager call. Implement it to delegate the decision elsewhere: a gateway's
rate-limit service (Envoy RLS), a SaaS limiter, or quotas per customer plan
read from your billing system.

```python
class PlanQuotas:
    async def caller(self, actor):  # each authenticated request
        plan = await plans.of(actor.tenant)
        if not await plans.take(actor.tenant, plan.per_minute):
            raise RateLimited("Plan quota reached", details={"retry_after": 60})
        return None  # or a Decision for the RateLimit headers

    async def client(self, address): ...  # requests without credentials
    async def before_auth(self, address): ...  # before credentials are checked
    async def failed(self, address): ...  # credentials were wrong
    async def action(self, actor, action): ...  # the manager is about to run an action
```

Pass it wherever a limiter goes: `ResourceManager(rate_limiter=)`,
`bearer_actor(..., rate_limiter=)`, `mount_graphql(rate_limiter=)`,
`ResourceMcpServer(rate_limiter=)`.

## Gateways

A gateway or WAF can still cap traffic in front of the app. The engine's
limits know who the caller is and which action they're running, which a
gateway doesn't.
