# Caching

The engine caches at two levels:

- **Records**, in the manager. Records read by id are kept in a `Cache` and
  forgotten when the engine commits a change to them.
- **HTTP answers**, in the client. Every `GET` answer carries an `ETag`, and
  a client that already has it gets `304 Not Modified`.

Both are safe with permissions. The record cache holds stored records, never
answers, and HTTP answers are private to the caller.

## Record cache

```python
from resource_engine.cache import InMemoryCache


class CustomerResource(Resource):
    cache_ttl = 300  # opt-in, in seconds


manager = ResourceManager(..., cache=InMemoryCache())
# several instances: one shared cache, so a write forgets the record everywhere
manager = ResourceManager(..., cache=RedisCache(redis, secret=os.environ["CACHE_SECRET"]))
```

- **What's cached:** `get` by id, which also covers the loads behind
  `allowed_transitions`, `history` and the GraphQL and MCP equivalents, for
  resources with `cache_ttl`. Lists, writes and everything inside
  `manager.transaction()` read the repository.
- **Permissions:** the cache holds the record as stored. Every read still
  runs the caller's permission check, row conditions, field rules and tenancy
  on it, so two callers reading the same cached record get their own answers.
- **Invalidation:** each write through the manager (update, delete,
  transition, action, cascades) forgets the record once its commit succeeds.
  Subtypes and their base share entries, so a write through either forgets
  both. A view with `cache_ttl` is never forgotten by writes: it expires.
- **Staleness:** a change made outside the manager (another service, SQL by
  hand, a migration) shows up when `cache_ttl` expires. A read racing a write
  can also store the old record just after it was forgotten. Choose
  `cache_ttl` as the staleness you accept, and leave resources whose readers
  must always see the latest write uncached.
- **Failures:** a cache that can't be read or written is a miss. The read
  goes to the repository and a warning is logged.

### Stores

| Cache | Package | Use |
|---|---|---|
| `InMemoryCache(max_entries=10_000)` | `resource-engine` | One instance, tests. With several instances, each one forgets only its own writes until entries expire. |
| `RedisCache(client, secret=...)` | `resource-engine-redis` | Shared by every instance. Entries are pickled and signed with HMAC-SHA256; unsigned or foreign entries are ignored, never unpickled. |

`Cache` (`resource_engine.ports.cache`) is the port: `get`, `set` with a TTL,
and `delete`. Implement it for Memcached or anything else.

## HTTP caching

`mount_resources` adds a middleware for `GET` answers with a JSON body:

- **ETag:** versioned resources use their version (`"3"`, the same one
  `If-Match` takes). Every other answer, lists included, gets a weak ETag of
  its body (`W/"…"`).
- **304:** a request with a matching `If-None-Match` gets `304 Not Modified`
  and no body. The client reuses its copy and saves the download and its
  parsing. The server still runs the request, so permissions and freshness
  are checked every time.
- **Cache-Control:** `private, no-cache` by default, meaning store but
  revalidate each time. A resource can set its own:
  `cache_control = "private, max-age=30"` lets the client reuse an answer for
  30 seconds without asking.
- **Vary:** `Authorization, X-API-Key`. Answers depend on who asks, so shared
  caches (CDNs, proxies) must not hand them to someone else.

`mount_resources(app, ..., http_cache=False)` turns it off. Downloads and
other non-JSON answers pass through untouched.

## Front ends

Browsers revalidate on their own: `fetch` sends `If-None-Match` when the
HTTP cache has the URL, and a 304 shows up as the cached 200. Data libraries
(TanStack Query, SWR, Apollo) keep their own cache on top. Refetch on focus
or after a mutation, and let ETags make the unchanged answers cheap.
