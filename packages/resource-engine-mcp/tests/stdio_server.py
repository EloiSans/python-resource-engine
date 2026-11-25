"""An MCP server over stdio on the invoices example (in memory), for test_stdio."""

import asyncio

from invoices.bootstrap import registry
from resource_engine import ResourceManager
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from resource_engine_mcp import ResourceMcpServer


class _Silent:
    async def send(self, message: str) -> None:
        pass


manager = ResourceManager(
    registry,
    InMemoryStore.from_registry(registry).unit_of_work,
    blob_store=InMemoryBlobStore(),
    services={"notifier": _Silent()},
)

if __name__ == "__main__":
    asyncio.run(ResourceMcpServer(registry, manager, name="invoices").run_stdio())
