"""ice/mcp_server.py — Model Context Protocol (MCP) server for ICE.

Owner:      Investigation Continuity Engine (ICE).
Purpose:    Exposes ICE's 46 cognitive, goal-evolution, investigative, and code-intelligence
            tools over stdio to MCP clients (Claude Code, Cursor, Windsurf, Antigravity).

Compatible with:
    - Claude Code
    - Cursor
    - Windsurf
    - Antigravity IDE
    - Custom MCP clients (via stdio JSON-RPC)

Registration example:
    {
      "mcpServers": {
        "ice": {
          "command": "python",
          "args": ["-m", "ice.mcp_server"],
          "cwd": "/path/to/ICE-improved"
        }
      }
    }
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure ice package directory is in sys.path
_ICE_DIR = Path(__file__).resolve().parent
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

from app.mcp.server import main, dispatch_request, PROTOCOL_VERSION
from app.mcp.tools_registry import TOOLS, get_mcp_schemas

if __name__ == "__main__":
    main()
