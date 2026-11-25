# resource-engine-mcp

MCP server for [Resource Engine](https://github.com/EloiSans/python-resource-engine)
applications. Every operation becomes a Model Context Protocol tool
(`invoices_list`, `invoices_transition_approve`...). Input schemas come from the
Pydantic models and descriptions from the functional docstrings. Each call goes
through the `ResourceManager` as the caller, with their permissions and an audit
entry.

```python
from resource_engine_mcp import ResourceMcpServer

mcp = ResourceMcpServer(registry, manager, name="invoices")

# local agents, over stdio, as a fixed principal
await mcp.run_stdio(actor=principal)

# Streamable HTTP in a FastAPI app, with bearer tokens
mcp.mount(
    app,
    authenticator=oidc,
    resource_url="https://api.example.com/mcp",
    issuer_url="https://login.example.com/realms/acme",
)
```

- **Tools per caller:** `tools/list` shows only what the caller may do.
- **Errors:** engine errors come back as tool errors carrying their `RE-*` code
  and hint.
- **Writes:** they accept an `idempotency_key`.

See [docs/mcp.md](https://github.com/EloiSans/python-resource-engine/blob/main/docs/mcp.md).
