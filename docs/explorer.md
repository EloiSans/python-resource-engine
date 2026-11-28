# Explorer

The explorer lets developers and operators try the running application from
the browser. It works like FastAPI's `/docs`, but covers the whole
application, not only its HTTP routes:

- **Operations:** each resource's HTTP operations, one form per operation,
  with the parameters and a JSON body to start from. A record read lists its
  allowed transitions; a refusal explains itself (`/authz/explain`); files
  (exports, printouts, documents) come as download links.
- **Events:** committed events, live, and which consumers handled each one.

The [documentation site](docs-site.md) is separate: it explains the
application for people who don't read code, and links the API's
`openapi.json` and `asyncapi.json`. The explorer is where the API is tried.

How it works:

- **The visitor's own token:** each call is made as that visitor, so they
  see and do only what they may.
- **Reads only by default:** changes need "Allow changes".
- **Off unless you turn it on:** nothing is served until the application
  mounts it.

## Starting it

```python
from resource_engine.explorer import register_explorer
from resource_engine_fastapi import mount_explorer

register_explorer(registry)  # its permissions, before the manager
manager = ResourceManager(registry, ..., event_publisher=TransportEventPublisher(hub))
mount_resources(app, registry, manager, actor_dependency=actor)
if settings.explorer:  # off in production unless you decide
    mount_explorer(app, manager, actor_dependency=actor, events=hub, activity=activity_log)
```

`http://localhost:8000/explorer/` serves one page with a tab for each part
the application enabled.

- **No build step and no devtools:** the page and its scripts ship with
  `resource-engine-fastapi` and are built from the running app's OpenAPI
  document.
- **Language:** English, Catalan or Spanish, from the browser's
  `Accept-Language` (or `?lang=ca`).
- **Endpoints:** they live under `api_prefix` (`/_explorer`), out of the
  application's OpenAPI document: they are a tool, not part of its API.

Arguments of `mount_explorer`:

| Argument | What it does |
|---|---|
| `actor_dependency` | The same dependency as `mount_resources`: who is calling |
| `events` | An `EventHub` fed with committed events: the Events tab |
| `activity` | An `ActivityLog`: which consumers handled each event |
| `path`, `api_prefix` | Where the page (`/explorer`) and its endpoints (`/_explorer`) are served |
| `open` | Allow the endpoints without an authorizer. **Local development only**: anyone could watch every event |
| `title`, `csp` | The page's title, and its Content-Security-Policy (the same default as `mount_docs`) |

## Permissions and audit

The explorer's actions belong to no resource. `register_explorer(registry)`
adds them to the authorization catalogue, so policies and the policy editor
see them like any other action ([permissions](permissions.md#actions)):

| Action | Allows |
|---|---|
| `events:read` | Every committed event with its payload, including events about no record |
| `jobs:read`, `jobs:run` | The worker's jobs (later parts of the explorer) |
| `events:publish:<Event>` | Publishing that event by hand (later parts). **Exact:** no wildcard grants it, not even `*` |

Without `events:read`, a caller still sees the events about records they can
read: their permission, row filter and tenant apply. They see the record as
they may read it, not the payload.

Each endpoint is audited:

- **Entries:** opening a stream and every refusal write an `audit_log`
  entry with `resource="explorer"` and the action.
- **No authorizer:** every endpoint is refused unless `open=True`.

## Events

The Events tab connects to `GET /_explorer/events/stream`, a stream of
server-sent events.

- **Filters:** resource (the AsyncAPI channel), event type, schema version
  and record id.
- **The list:** it keeps the last 500 events. Pause stops the list without
  dropping events, and Clear empties it.
- **An event's details:** click one to see its envelope; with `events:read`,
  its payload too. Otherwise you see the record as you can read it now. The
  details also show which consumers handled the event, with their outcome,
  duration and error.

The stream's frames look like this:

```text
event: event
id: 6f1c…
data: {"envelope": {...}, "resource": "invoices", "entity_id": 7, "record": {...}, "payload_visible": false}
```

How the stream behaves:

- **Keepalive:** a comment every 15 seconds keeps proxies from closing a
  quiet stream.
- **No `EventSource`:** the page reads the stream with `fetch`, because
  `EventSource` can't send the `Authorization` header.
- **The same rules as GraphQL:** the stream follows the GraphQL
  subscriptions' rules (both use `LiveEvents`). A record is re-read as the
  subscriber before each event about it. A deletion reaches only
  subscribers who already saw that record, so ids they never saw stay
  hidden.

### Feeding the hub

The hub receives committed events the same way as for
[GraphQL subscriptions](graphql.md#subscriptions):

- **One process:** `TransportEventPublisher(hub)` right after commit.
- **From the outbox:** `FanoutPublisher(broker, hub)`.
- **Several instances:** each consumes a broadcast topic into
  `hub.router()`.

### Which consumers handled an event

`GET /_explorer/events/{message_id}/deliveries` reads an `ActivityLog`. The
worker's consumers write to it through `ActivityMessageObserver`:

```python
from resource_engine.messaging import ActivityMessageObserver
from resource_engine_sqlalchemy import SqlAlchemyActivityLog, engine_tables

tables = engine_tables(metadata, ..., activity=True)
activity_log = SqlAlchemyActivityLog(sessions, tables.activity)
processor = MessageProcessor(
    router, name="billing", manager=manager, observers=[ActivityMessageObserver(activity_log)]
)
```

How the log is kept:

- **Shared:** the API and the worker share the log's tables, so the
  explorer sees what the worker did.
- **Tests and single processes:** `InMemoryActivityLog` does the same in
  memory.
- **Retention:** `MessagingJanitor(activity=activity_log,
  activity_retention=timedelta(days=7))` deletes old records.

## In production

- **Opt-in:** mount the explorer only when a setting asks for it, and
  never with `open=True`.
- **Who gets what:**
  - `events:read`: operators.
  - `jobs:*`: administrators.
  - `events:publish:<Event>`: only the roles that need it, one event at a
    time.
- **Rate limits** apply as for any request: the token check and
  `bearer_actor(rate_limiter=...)`.
