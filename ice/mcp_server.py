"""ice/mcp_server.py — Model Context Protocol (MCP) server for ICE.

Owner:      Investigation Continuity Engine (ICE).
Purpose:    Exposes ICE's cognitive capabilities (Evolving Project Model,
            STORM-lite Research, Serendipity Engine, and RepoIntel code grounding)
            as standardized MCP tools over stdio.

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

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure package root is in path
_ICE_DIR = Path(__file__).resolve().parent
if str(_ICE_DIR) not in sys.path:
    sys.path.insert(0, str(_ICE_DIR))

from case_engine.repointel_client import RepoIntelClient  # noqa: E402

logger = logging.getLogger("ice.mcp")

PROTOCOL_VERSION = "2026-07-28"

TOOLS = [
    {
        "name": "ice_create_goal",
        "description": "Create or revise a project intention in the Evolving Project Model. "
        "Supports clarity levels 1 (vague conversation) to 4 (formal specification).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The active case ID"},
                "statement": {"type": "string", "description": "Goal statement / objective"},
                "kind": {
                    "type": "string",
                    "enum": [
                        "goal", "need", "actor", "scenario", "requirement",
                        "quality_objective", "constraint", "assumption", "decision",
                        "alternative", "risk", "open_question", "milestone"
                    ],
                    "default": "goal"
                },
                "clarity_level": {"type": "integer", "enum": [1, 2, 3, 4], "default": 1},
                "origin": {
                    "type": "string",
                    "enum": [
                        "developer_statement", "developer_confirmed",
                        "ice_inference", "ice_proposal", "repo_observation"
                    ],
                    "default": "developer_statement"
                },
                "supersedes": {"type": "string", "description": "Optional prior goal ID being revised"}
            },
            "required": ["case_id", "statement"]
        }
    },
    {
        "name": "ice_list_goals",
        "description": "List all goals, requirements, constraints, and open questions for a case.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The case ID"},
                "kind": {"type": "string", "description": "Optional filter by kind"},
                "include_superseded": {"type": "boolean", "default": False}
            },
            "required": ["case_id"]
        }
    },
    {
        "name": "ice_reconcile_goals",
        "description": "Reconcile active project goals against codebase and evidence. "
        "Classifies each goal as aligned, accidental_divergence, intentional_evolution, or unresolved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The case ID"}
            },
            "required": ["case_id"]
        }
    },
    {
        "name": "ice_intent_profile",
        "description": "Generate an intent profile summarizing what the project is doing and what open questions remain before or during implementation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The case ID"}
            },
            "required": ["case_id"]
        }
    },
    {
        "name": "ice_research_brief",
        "description": "Run STORM-lite research mode: discover perspectives, generate information-gap questions, and produce a tiered synthesis outline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The case ID"},
                "max_gaps": {"type": "integer", "default": 6}
            },
            "required": ["case_id"]
        }
    },
    {
        "name": "ice_verify_code_maturity",
        "description": "Inspect repository AST and call edges (via RepoIntel) to evaluate goal code maturity (scaffolded, candidate, connected, tested).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "statement": {"type": "string"},
                "kind": {"type": "string", "default": "goal"}
            },
            "required": ["goal_id", "statement"]
        }
    },
    {
        "name": "ice_suggest_next",
        "description": "Run the Serendipity Engine (DPP diversity & multi-armed bandit) to suggest the next diverse action, open question, or perspective to explore.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "description": "The case ID"},
                "context_tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["case_id"]
        }
    },
    {
        "name": "ice_ingest_evidence",
        "description": "Ingest code findings, observations, or facts into ICE's persistent case graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "content": {"type": "string"},
                "evidence_type": {"type": "string", "default": "TOOL_RESULT"},
                "metadata": {"type": "object"}
            },
            "required": ["case_id", "content"]
        }
    }
]

DISCOVERY = {
    "protocolVersion": PROTOCOL_VERSION,
    "serverInfo": {"name": "ice-continuity-engine", "version": "1.2.0"},
    "stateless": True,
    "capabilities": {"tools": {"list": True, "call": True}},
    "methods": ["server/discover", "tools/list", "tools/call", "ping"],
    "deprecated": ["initialize"],
}


async def execute_tool(name: str, arguments: dict) -> dict:
    """Execute the requested ICE tool and format as MCP content."""
    try:
        if name == "ice_verify_code_maturity":
            client = RepoIntelClient()
            assessment = client.check_goal_code_maturity(
                goal_id=arguments["goal_id"],
                statement=arguments["statement"],
                target_kind=arguments.get("kind", "goal")
            )
            return {
                "content": [{"type": "text", "text": json.dumps(assessment.__dict__, indent=2)}]
            }

        if name == "ice_create_goal":
            from case_engine.goals import create_goal
            res = await create_goal(
                case_id=arguments["case_id"],
                statement=arguments["statement"],
                kind=arguments.get("kind", "goal"),
                clarity_level=arguments.get("clarity_level", 1),
                origin=arguments.get("origin", "developer_statement"),
                supersedes=arguments.get("supersedes")
            )
            return {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}

        if name == "ice_list_goals":
            from case_engine.goals import list_goals
            res = await list_goals(
                case_id=arguments["case_id"],
                kind=arguments.get("kind"),
                include_superseded=arguments.get("include_superseded", False)
            )
            return {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}

        if name == "ice_reconcile_goals":
            from case_engine.goals import reconcile_goals
            res = await reconcile_goals(arguments["case_id"])
            return {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}

        if name == "ice_intent_profile":
            from case_engine.goals import intent_profile
            res = await intent_profile(arguments["case_id"])
            return {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}

        if name == "ice_research_brief":
            from case_engine.research import research_brief
            res = await research_brief(arguments["case_id"], max_gaps=arguments.get("max_gaps", 6))
            return {"content": [{"type": "text", "text": json.dumps(res, default=str, indent=2)}]}

        if name == "ice_suggest_next":
            # Native Serendipity Engine suggestion
            _SE_DIR = _ICE_DIR / "serendipity-engine"
            if str(_SE_DIR) not in sys.path:
                sys.path.insert(0, str(_SE_DIR))
            from ice_adapter.adapter import IceSerendipityAdapter
            from case_engine.goals import list_goals

            active_goals = await list_goals(arguments["case_id"], include_superseded=False)
            items = IceSerendipityAdapter.goals_to_items(active_goals)
            suggestions = [
                {"id": it.id, "title": it.title, "category": it.category, "quality": it.quality}
                for it in items[:5]
            ]
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"case_id": arguments["case_id"], "diverse_candidates": suggestions}, indent=2)
                }]
            }

        if name == "ice_ingest_evidence":
            from case_engine.evidence import ingest_evidence, _create_source
            src = await _create_source(arguments["case_id"], "TOOL", source_uri="mcp://client")
            ev = await ingest_evidence(
                case_id=arguments["case_id"],
                content=arguments["content"],
                source_id=src.source_id,
                evidence_type=arguments.get("evidence_type", "TOOL_RESULT"),
                metadata=arguments.get("metadata", {})
            )
            return {"content": [{"type": "text", "text": json.dumps({"evidence_id": ev.evidence_id, "status": "INGESTED"})}]}

        return {"content": [{"type": "text", "text": f"Error: unknown tool '{name}'"}]}

    except Exception as exc:
        logger.exception("Error executing tool %s: %s", name, exc)
        return {"content": [{"type": "text", "text": f"Execution error: {exc}"}]}


async def dispatch_request(request: dict) -> dict | None:
    """Handle incoming MCP JSON-RPC requests."""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "server/discover":
        return {"jsonrpc": "2.0", "id": req_id, "result": DISCOVERY}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": DISCOVERY["serverInfo"],
            }
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = await execute_tool(name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method '{method}' not found"}
    }


async def main():
    """Stdio JSON-RPC server loop."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        text = line.decode("utf-8").strip()
        if not text:
            continue
        try:
            req = json.loads(text)
            resp = await dispatch_request(req)
            if resp is not None:
                out = json.dumps(resp) + "\n"
                sys.stdout.write(out)
                sys.stdout.flush()
        except Exception as exc:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
