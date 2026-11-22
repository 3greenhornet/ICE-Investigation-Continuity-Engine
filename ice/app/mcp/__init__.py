"""ICE MCP server package.

Exposes main, dispatch_request, TOOLS, and get_mcp_schemas.
"""

from app.mcp.server import main, dispatch_request, PROTOCOL_VERSION
from app.mcp.tools_registry import TOOLS, get_mcp_schemas

__all__ = ["main", "dispatch_request", "TOOLS", "get_mcp_schemas", "PROTOCOL_VERSION"]
