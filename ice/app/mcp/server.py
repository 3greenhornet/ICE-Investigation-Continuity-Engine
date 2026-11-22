"""server.py — Stdio JSON-RPC 2.0 MCP Server for ICE.

Exposes ICE's 46 cognitive, goal-evolution, investigative, and code-intelligence
tools over stdio to MCP clients (Claude Code, Cursor, Windsurf, Antigravity).

Usage:
    cd /path/to/ICE-improved/ice
    python -m app.mcp.server

Registration in Claude Code / Cursor:
    {
      "mcpServers": {
        "ice": {
          "command": "python",
          "args": ["-m", "app.mcp.server"],
          "cwd": "/path/to/ICE-improved/ice"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure package roots are importable
_ICE_DIR = Path(__file__).resolve().parents[2]
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

from app.mcp.tools_registry import TOOLS, get_mcp_schemas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ice.mcp.server")

PROTOCOL_VERSION = "2026-07-28"


async def dispatch_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch incoming JSON-RPC 2.0 request."""
    method = request.get("method", "")
    req_id = request.get("id")

    # 1. Stateless protocol discovery (MCP 2026-07-28)
    if method == "server/discover":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "ice-investigation-engine", "version": "2.1.0"},
                "stateless": True,
                "capabilities": {"tools": {"list": True, "call": True}},
                "methods": ["server/discover", "tools/list", "tools/call", "ping"],
                "deprecated": ["initialize"],
            },
        }

    # 2. Legacy / compatibility initialize handshake
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ice-investigation-engine", "version": "2.1.0"},
                "deprecated": True,
            },
        }

    # 3. Lifecycle notifications & ping
    if method in ("notifications/initialized", "ping"):
        return None

    # 4. Tool listing
    if method == "tools/list":
        schemas = get_mcp_schemas()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": schemas},
        }

    # 5. Tool execution
    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: '{tool_name}'"},
            }

        try:
            fn = TOOLS[tool_name]["fn"]
            raw_res = await fn(**arguments)
            formatted_text = json.dumps(raw_res, default=str, indent=2)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": formatted_text}],
                    "structured": raw_res,
                },
            }
        except Exception as exc:
            logger.exception("Failed executing tool '%s': %s", tool_name, exc)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": f"Tool execution failed: {str(exc)}"},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: '{method}'"},
    }


async def run_server():
    """Async line reader for stdin/stdout communication."""
    logger.info("ICE MCP Server starting (protocol version %s, %d tools)", PROTOCOL_VERSION, len(TOOLS))
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line_bytes = await reader.readline()
        if not line_bytes:
            break
        line = line_bytes.decode("utf-8").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        resp = await dispatch_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + "\n")
            sys.stdout.flush()


def main():
    """Main entrypoint for MCP stdio server."""
    try:
        asyncio.run(run_server())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
