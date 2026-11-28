# Explorer

The explorer lets developers and operators try the running application from
the browser. It looks like Swagger UI, FastAPI's `/docs`: a section per
resource and a row per operation, each opening into its "Try it" form. Next
to the HTTP verbs it lists what else the application does:

| Verb | Row | Try it |
|---|---|---|
| `GET` `POST` `PUT` `PATCH` `DELETE` | An HTTP operation (`/invoices/{entity_id}`) | Its parameters and a JSON body to start from. A record read lists its allowed transitions; a refusal explains itself (`/authz/explain`); files (exports, printouts, documents) come as download links |
| `SUB` | An event type a resource publishes (`invoices/ResourceCreated`, with its schema version) | Its committed events, live, filtered by record id; each with its envelope, payload, record and which consumers handled it |
| `CRON` | A timed transition (`invoices/reject`), or a worker job in the `worker` section | When it fires and when it is due for one record; a job's schedule, runs and "run now" |
| `PUB` | An event the application lets you publish by hand (coming next) | — |

Inside each resource the rows are grouped in collapsible blocks by what they
do. Only CRUD is open at first, and each header shows how many rows it holds
and which verbs:

- **CRUD:** list, read, create, update, delete.
- **Transitions and actions:** the state machine's transitions, the
  resource's actions, and its history.
- **Relations:** related records (`/customers/{id}/invoices`).
- **Documents:** attaching, uploading and removing documents.
- **Export and print:** the exports and printouts.
- **Events (pub/sub):** the `SUB` (and `PUB`) rows.
- **Schedule (cron):** the timed transitions.

A box at the top filters the rows by verb, path or description. While it
filters, the blocks with a match open and the others are hidden.

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
    mount_explorer(
        app, manager, actor_dependency=actor, events=hub, activity=activity_log, jobs=True
    )
```

`http://localhost:8000/explorer/` serves the page. `SUB` rows need `events`,
and `CRON` rows need `jobs`.

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
| `events` | An `EventHub` fed with committed events: the `SUB` rows |
| `activity` | An `ActivityLog`: which consumers handled each event, and the workers' jobs |
| `jobs` | The `CRON` rows: needs `activity`, the same log the workers write to |
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
| `jobs:read`, `jobs:run` | The workers' jobs and their runs; asking for a run now |
| `events:publish:<Event>` | Publishing that event by hand (later parts). **Exact:** no wildcard grants it, not even `*` |

Without `events:read`, a caller still sees the events about records they can
read: their permission, row filter and tenant apply. They see the record as
they may read it, not the payload.

Each endpoint is audited:

- **Entries:** opening a stream and every refusal write an `audit_log`
  entry with `resource="explorer"` and the action.
- **No authorizer:** every endpoint is refused unless `open=True`.

## Events

Each `SUB` row connects to `GET /_explorer/events/stream`, a stream of
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

- **One process:** `TransportEventPublisher(hub)` right after commit, or
  with an outbox `OutboxDelivery(live=hub)`: the same envelopes, so the
  consumers' deliveries match the events shown.
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

## Jobs

The `CRON` rows show what the running workers do, and lets administrators run
a job now.

The worker and the API are separate processes, so they meet in the
`ActivityLog`. A runner built with it reports there:

```python
activity_log = SqlAlchemyActivityLog(sessions, tables.activity)
runner = JobRunner(locks=SqlAlchemyLocks(...), activity=activity_log)  # in the worker
mount_explorer(app, manager, ..., activity=activity_log, jobs=True)  # in the API
```

What the runner reports:

- **Its jobs:** it registers its jobs and their schedules when it starts,
  and again every `heartbeat` (60 s), so the explorer lists the workers
  seen recently. It needs no code from the worker.
- **Every run:** it records each run with its outcome, duration and error.
  `skipped` means another worker held the lease of a `single` job.
- **Run now:** the API records a request. The worker claims it within
  `requests_every` (2 s) and runs the job through `run_job`. Only one
  worker claims each request, and a `single` job still takes its lease: if
  another worker holds it, the request ends `skipped` instead of running
  twice.

The endpoints:

| Endpoint | Permission | What it does |
|---|---|---|
| `GET /_explorer/jobs` | `jobs:read` | Every registered job: schedule, `single` and lease, workers, last run |
| `GET /_explorer/jobs/{name}/runs` | `jobs:read` | Its recent runs |
| `POST /_explorer/jobs/{name}/run` | `jobs:run` (audited) | 202 with a request; 404 for a job no worker runs |
| `GET /_explorer/jobs/requests/{id}` | `jobs:read` | `pending`, `running`, `ok`, `failed` or `skipped` |
| `GET /_explorer/timers/{resource}/{id}` | `get` on the record | The [timed transitions](timers.md) waiting on it and when they are due |

In the page, "Run now" needs "Allow changes", like any write. The page then
follows the request until a worker finishes it. The timers form shows each
transition waiting on a record, with its due time; overdue ones run on the
next `TimerJob` run.

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
