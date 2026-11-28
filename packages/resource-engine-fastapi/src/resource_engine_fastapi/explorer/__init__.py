"""The explorer: try the running application from the browser, like FastAPI's
``/docs`` but for the whole application (docs/explorer.md).

```python
from resource_engine_fastapi.explorer import mount_explorer

if settings.explorer:                                   # off unless you turn it on
    mount_explorer(app, manager, actor_dependency=actor, events=hub, activity=log)
# http://localhost:8000/explorer/
```

Laid out like Swagger UI: a section per resource, a row per verb.

- ``GET`` ``POST`` ``PUT`` ``PATCH`` ``DELETE``: every HTTP operation, with its form.
- ``SUB``: each event type a resource publishes, live (``events=`` an
  ``EventHub``), and which consumers handled each one (``activity=`` an
  ``ActivityLog``).
- ``CRON`` (``jobs=True``, with ``activity``): each timed transition, and each
  worker job with its runs and "run now".

Everything calls the running API with the visitor's own token: they see and
do exactly what they may. Reads only, until they tick "Allow changes". The
explorer's own endpoints live under ``api_prefix`` (``/_explorer``), out of
the application's OpenAPI document (they are a tool, not its API), behind
their permissions (``register_explorer`` adds them to the catalogue) and
audited. Without an authorizer they are refused, unless ``open=True`` (local
development only).

The documentation site (``resource-engine docs site``, ``mount_docs``) is the
functional documentation and links the API's documents; the explorer is
where the API is tried.
"""

from __future__ import annotations

import html
import json
from importlib.resources import files
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from resource_engine_fastapi.docs import CSP
from resource_engine_fastapi.explorer import data, labels
from resource_engine_fastapi.explorer.data import console_data
from resource_engine_fastapi.explorer.labels import locales

ASSETS = ("console.js", "events.js", "jobs.js", "explorer.js", "explorer.css")


def asset(name: str) -> str:
    """One of the explorer's assets (``console.js``...), as text."""
    return (files(__package__) / "assets" / name).read_text(encoding="utf-8")


def _no_actor() -> None:
    return None


def event_types(registry: Any) -> list[dict[str, Any]]:
    """Event types the application publishes, with their schema versions."""
    from resource_engine.application import events as lifecycle
    from resource_engine.application.events import ResourceEvent
    from resource_engine.documents import events as documents
    from resource_engine.documents.events import DocumentEvent
    from resource_engine.domain.events import DomainEvent

    found: dict[str, Any] = {}
    for module in (lifecycle, documents):
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, DomainEvent)
                and value not in (DomainEvent, ResourceEvent, DocumentEvent)
                and value.__module__ == module.__name__
            ):
                found[value.__name__] = value
    for event in registry.event_types():
        found[event.__name__] = event
    out = []
    for name, event in sorted(found.items()):
        current = int(getattr(event, "schema_version", 1))
        older = sorted(int(v) for v in (getattr(event, "previous_versions", None) or {}))
        out.append({"type": name, "versions": [str(v) for v in [*older, current]]})
    return out


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n\n")[0].replace("\n", " ").strip()


def event_channels(registry: Any) -> dict[str, list[dict[str, Any]]]:
    """The event types each resource publishes (its AsyncAPI channel): the
    explorer's SUB rows. Only resources whose records can be read."""
    from resource_engine.messaging.asyncapi import asyncapi_document

    document = asyncapi_document(registry)
    messages = document.get("components", {}).get("messages", {})
    out: dict[str, list[dict[str, Any]]] = {}
    for name, channel in document.get("channels", {}).items():
        if name not in registry or not registry.get(name).exposed("get"):
            continue
        rows = []
        for ref in channel.get("messages", {}).values():
            message = messages.get(str(ref.get("$ref", "")).rsplit("/", 1)[-1], {})
            headers = message.get("headers", {}).get("properties", {})
            rows.append(
                {
                    "type": message.get("name"),
                    "version": str(headers.get("schema_version", {}).get("const", "1")),
                    "summary": message.get("summary") or "",
                    "deprecated": "x-deprecation" in message,
                }
            )
        out[name] = rows
    return out


def timer_rows(registry: Any) -> dict[str, list[dict[str, Any]]]:
    """Each resource's timed transitions: the explorer's CRON rows."""
    return {
        d.name: [
            {
                "event": t.event,
                "after_seconds": t.after.total_seconds(),
                "since": t.since,
                "states": [str(getattr(s, "value", s)) for s in t.states],
                "description": t.description,
            }
            for t in d.timers
        ]
        for d in registry
        if d.timers
    }


def explorer_config(
    app: Any,
    manager: Any,
    *,
    api_prefix: str,
    events: bool,
    deliveries: bool,
    locale: str | None,
    jobs: bool = False,
) -> dict[str, Any]:
    """What the explorer's scripts read as ``window.CONSOLE``."""
    openapi = app.openapi()
    names = [d.name for d in manager.registry]
    config = console_data(openapi, names)
    config.update(
        api="",
        me="/authz/me" in openapi.get("paths", {}),
        labels=labels.labels(locale),
        groups=[{"name": d.name, "summary": _first_line(d.description)} for d in manager.registry],
    )
    if events:
        config["events"] = {
            "stream": f"{api_prefix}/events/stream",
            "deliveries": f"{api_prefix}/events/{{message_id}}/deliveries" if deliveries else None,
            "resources": [d.name for d in manager.registry if d.exposed("get")],
            "types": event_types(manager.registry),
            "channels": event_channels(manager.registry),
        }
    if jobs:
        config["jobs"] = {
            "list": f"{api_prefix}/jobs",
            "runs": f"{api_prefix}/jobs/{{name}}/runs",
            "run": f"{api_prefix}/jobs/{{name}}/run",
            "request": f"{api_prefix}/jobs/requests/{{id}}",
            "timers": f"{api_prefix}/timers/{{resource}}/{{id}}",
            "timer_resources": [d.name for d in manager.registry if d.timers],
            "timers_by_resource": timer_rows(manager.registry),
        }
    return config


def _locale(request: Request) -> str | None:
    """``?lang=``, else the browser's first offered language we have."""
    offered = locales()
    wanted = request.query_params.get("lang")
    candidates = [wanted] if wanted else []
    for part in request.headers.get("accept-language", "").split(","):
        candidates.append(part.split(";")[0].strip())
    for candidate in candidates:
        if not candidate:
            continue
        base = candidate.lower().split("-")[0]
        if base in offered:
            return base
    return None


def _page(title: str, locale: str | None, root: str) -> str:
    label = labels.labels(locale)
    config = f"config.js?lang={html.escape(locale)}" if locale else "config.js"
    scripts = "\n".join(
        f'<script src="{root}/{name}"></script>'
        for name in (
            config,
            "assets/console.js",
            "assets/events.js",
            "assets/jobs.js",
            "assets/explorer.js",
        )
    )
    return f"""<!doctype html>
<html lang="{html.escape(locale or "en")}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(label["explorer"])} · {html.escape(title)}</title>
<link rel="stylesheet" href="{root}/assets/explorer.css">
</head>
<body>
<header><h1>{html.escape(label["explorer"])}</h1>
<span class="app">{html.escape(title)}</span></header>
<main><div id="explorer" class="console"></div></main>
{scripts}
</body>
</html>
"""


def mount_explorer(
    app: Any,
    manager: Any,
    *,
    path: str = "/explorer",
    api_prefix: str = "/_explorer",
    actor_dependency: Any = _no_actor,
    events: Any = None,
    activity: Any = None,
    jobs: bool = False,
    open: bool = False,
    title: str | None = None,
    csp: str | None = CSP,
) -> None:
    """Serve the explorer at ``path`` and its endpoints at ``api_prefix``.

    Args:
        actor_dependency: The same dependency as ``mount_resources``'s.
        events: An ``EventHub`` fed with committed events: the ``SUB`` rows.
        activity: An ``ActivityLog``: which consumers handled each event, and
            (with ``jobs``) the workers' jobs and runs.
        jobs: The ``CRON`` rows: needs ``activity``, and workers built with
            ``JobRunner(activity=...)`` on the same log.
        open: Allow the explorer's endpoints without an authorizer (local
            development only: anyone could see every event).
        csp: Content-Security-Policy of the page.
    """
    api = APIRouter(prefix=api_prefix, include_in_schema=False)
    if events is not None:
        from resource_engine_fastapi.explorer.events import build_events_router

        api.include_router(
            build_events_router(
                manager, events, actor_dependency=actor_dependency, activity=activity, open=open
            )
        )
    if jobs:
        if activity is None:
            from resource_engine.errors import ConfigurationError

            raise ConfigurationError(
                "The explorer's jobs need the activity log the workers write to",
                hint="mount_explorer(..., jobs=True, activity=SqlAlchemyActivityLog(...)) and "
                "JobRunner(activity=<the same log>).",
                doc="docs/explorer.md#jobs",
            )
        from resource_engine_fastapi.explorer.jobs import build_jobs_router

        api.include_router(
            build_jobs_router(manager, activity, actor_dependency=actor_dependency, open=open)
        )
    app.include_router(api)
    root = path.rstrip("/")
    page_title = title or getattr(app, "title", None) or "API"
    headers = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
    if csp:
        headers["Content-Security-Policy"] = csp
    ui_router = APIRouter(prefix=root, include_in_schema=False)

    @ui_router.get("/", response_class=HTMLResponse)
    async def page(request: Request) -> HTMLResponse:
        return HTMLResponse(
            _page(page_title, _locale(request), root),
            headers={**headers, "Cache-Control": "no-store"},
        )

    @ui_router.get("/config.js")
    async def config(request: Request) -> Response:
        payload = explorer_config(
            app,
            manager,
            api_prefix=api_prefix,
            events=events is not None,
            deliveries=activity is not None,
            locale=_locale(request),
            jobs=jobs,
        )
        body = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return Response(
            f"window.CONSOLE = {body};\n",
            media_type="text/javascript",
            headers={**headers, "Cache-Control": "no-store"},
        )

    @ui_router.get("/assets/{name}")
    async def assets(name: str) -> Response:
        if name not in ASSETS:
            return Response(status_code=404)
        kind = "text/css" if name.endswith(".css") else "text/javascript"
        return Response(asset(name), media_type=kind, headers=headers)

    app.include_router(ui_router)
    if root:  # /explorer → /explorer/

        @app.get(root, include_in_schema=False)
        async def redirect() -> Response:
            return Response(status_code=307, headers={"Location": f"{root}/"})


__all__ = ["asset", "data", "event_types", "explorer_config", "labels", "mount_explorer"]
