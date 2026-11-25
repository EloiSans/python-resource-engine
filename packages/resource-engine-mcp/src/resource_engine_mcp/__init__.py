"""MCP server for Resource Engine applications: every operation as a tool.

See docs/mcp.md.
"""

from resource_engine_mcp.server import (
    PrincipalAccessToken,
    PrincipalVerifier,
    ResourceMcpServer,
    token_principal,
)
from resource_engine_mcp.tools import ResourceTool, build_tools

__all__ = [
    "PrincipalAccessToken",
    "PrincipalVerifier",
    "ResourceMcpServer",
    "ResourceTool",
    "build_tools",
    "token_principal",
]
