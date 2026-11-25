# MCP server

`resource-engine-mcp` exposes an application's operations to AI agents as
[Model Context Protocol](https://modelcontextprotocol.io) tools. Each tool is a
`ResourceManager` call made as the caller, so permissions, tenancy, validation,
hooks, events and the audit trail apply exactly as they do over HTTP. The
server adds no business logic.

```python
from resource_engine_mcp import ResourceMcpServer

mcp = ResourceMcpServer(registry, manager, name="invoices")
```

## Tools

Every registered resource gets one tool per enabled operation:

| Tool | Manager call | Arguments | Annotations |
|---|---|---|---|
| `<resource>_list` | `list` | `offset`, `limit`, `order_by`, `filters` | read-only |
| `<resource>_get` | `get` | `id` | read-only |
| `<resource>_create` | `create` | `data` | |
| `<resource>_update` | `update` | `id`, `data`, `expected_version` | |
| `<resource>_delete` | `delete` | `id`, `expected_version` | destructive |
| `<resource>_allowed_transitions` | `allowed_transitions` | `id` | read-only |
| `<resource>_history` | `history` | `id` | read-only |
| `<resource>_transition_<event>` | `transition` | `id`, `data` (with a transition schema), `expected_version` | |
| `<resource>_action_<name>` | `run_action` | `id` (instance actions), `data` (with an input schema), `expected_version` | |

The transition tools exist for resources with a state machine and
`expose_transitions`. The history tool also needs an audit trail stored as a
resource (`ResourceManager(audit=True)`; see [audit](audit.md#history)).

- **Writes** accept `idempotency_key`. The same call with the same key runs
  once, and later calls return the first result (see
  [idempotency](idempotency.md)). Agents retry, so tell them to send one.
- **Tenant-scoped resources** take `tenant`, which only cross-tenant callers may
  set (see [tenancy](tenancy.md)).
- **Input schemas** come from the resource's Pydantic schemas: `data` is the
  create, update, transition or action schema, and `filters` lists the
  `filterable` fields with their types.
- **Output schemas** come from the read schema. None of their fields is
  required, because field-level permissions may hide some of them.
- **Descriptions** come from the functional docstrings: `operation_docs`, the
  action's description, and the resource and state machine docstrings. A
  transition's description lists the states it moves between (`pending →
  rejected`).
- **Documents**: uploads and downloads are not tools. The `documents` resource
  appears with its `list` and `get` tools.

Narrow the set with `resources=` (resource names), and `include=` or `exclude=`
(tool names or shell patterns; `exclude` wins):

```python
ResourceMcpServer(registry, manager, include=["invoices_*"], exclude=["*_delete", "audit_log_*"])
```

A tool name that MCP does not allow (letters, digits, `_`, `-` and `.`, at most
128 characters), or two tools with the same name, raise `ConfigurationError`
when the server is built.

## Who is calling

The manager receives an `actor` for every call:

- **HTTP:** the `Principal` of the request's bearer token.
- **Otherwise:** the server's fixed `actor=`, which suits stdio and service
  accounts.
- **Custom:** `resolve_actor=lambda ctx: ...`, sync or async, for any other
  scheme.

`tools/list` shows only the tools the caller may use. The server checks each
tool's action (`update`, `transition:approve`, `action:recalculate_totals`) with
`manager.can` without a record. A rule with a condition counts as allowed,
because the record decides. `filter_tools=False` lists every tool.

Calls are always checked by the manager, whether or not the list was filtered.
A refused call returns RE-E702 like any other error.

## Errors

Engine and domain errors come back as tool results with `isError: true`. The
error's code, message, hint, doc link and details are in the structured content
under `error`, so the agent can read the hint and correct itself:

```json
{"error": {"code": "RE-E201", "message": "Transition 'approve' is not allowed for 'invoices' in state 'draft'",
           "hint": "Allowed transitions from 'draft': submit. ...", "details": {"state": "draft", "allowed": ["submit"]}}}
```

Other exceptions are logged on `resource_engine.mcp`, and the agent sees only
`Internal error`.

## stdio

For a local agent (Claude Code, Claude Desktop, an IDE), start a process that
serves one client over stdin and stdout:

```python
# billing/mcp_server.py
import asyncio

from resource_engine.authz import system_principal
from resource_engine_mcp import ResourceMcpServer

from billing.bootstrap import build_manager, registry

if __name__ == "__main__":
    mcp = ResourceMcpServer(registry, build_manager(), name="billing")
    asyncio.run(mcp.run_stdio(actor=system_principal("billing-agent")))
```

```json
{"mcpServers": {"billing": {"command": "uv", "args": ["run", "python", "-m", "billing.mcp_server"]}}}
```

Every call runs as that principal. Give it a role with only what the agent
needs.

## HTTP

`mount` adds Streamable HTTP to a FastAPI or Starlette app. It also runs the
MCP session manager in the app's lifespan. Call it after `mount_resources`,
because the endpoint is mounted after the app's own routes.

```python
mount_resources(app, registry, manager, actor_dependency=bearer_actor(oidc))
mcp.mount(
    app,
    path="/mcp",
    authenticator=oidc,  # the same Authenticator as the HTTP API
    resource_url="https://api.example.com/mcp",
    issuer_url="https://login.example.com/realms/acme",
)
```

- **With `authenticator`:** requests need `Authorization: Bearer <token>`. A
  missing or invalid token gets a 401 with `WWW-Authenticate`.
- **With `issuer_url` as well:** the server publishes OAuth protected resource
  metadata (RFC 9728) at `/.well-known/oauth-protected-resource/mcp`. MCP clients
  read it to find the authorization server and sign the user in.
- **Audience:** the `Authenticator` checks the token's audience (OIDC
  `audience=`), so issue tokens for the API.
- **Without `authenticator`:** calls are anonymous, or use the fixed `actor`.
  This is for development only.

`http_app(...)` returns the Starlette app on its own. When you mount it
yourself, run `mcp.server.session_manager.run()` in your lifespan. Other
options go to the SDK's `streamable_http_app`:

- `stateless_http=True` for horizontally scaled servers without sticky
  sessions;
- `json_response=True` for plain JSON replies instead of SSE;
- `transport_security` and `host` for DNS rebinding protection, which is on for
  localhost by default.

## Observability and audit

A tool call is a manager operation, so it has:

- an operation span and the `OperationObserver` metrics;
- a log context with a correlation id (see [observability](observability.md));
- an audit entry naming the principal (see [audit](audit.md)).

The MCP SDK adds its own server span per message when OpenTelemetry is
configured.
