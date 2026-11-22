"""repointel_bridge.py — Resilient Hybrid Bridge: ICE -> RepoIntel.

Enables ICE to invoke RepoIntel tools on demand with a dual-mode fallback:
1. HTTP Client: Attempts live call to RepoIntel's API / MCP over HTTP (default http://localhost:8000).
2. SQLite Fallback: If RepoIntel HTTP server is offline or unreachable, falls back to direct
   read-only SQLite inspection (data/repointel.db) via RepoIntelClient.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

from case_engine.repointel_client import RepoIntelClient

logger = logging.getLogger("ice.mcp.bridge")

_REPOINTEL_URL = os.environ.get("REPOINTEL_URL", "http://localhost:8000")
_TIMEOUT = 10.0


class RepoIntelBridge:
    """Bidirectional bridge: ICE -> RepoIntel.

    Usage:
        bridge = RepoIntelBridge()
        result = await bridge.invoke_tool("get_impact", {"repo_id": "main", "symbol": "login"})
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or _REPOINTEL_URL).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT)
        self._sqlite_client = RepoIntelClient()

    async def close(self):
        await self._client.aclose()

    async def invoke_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call any RepoIntel tool by name with automatic direct-DB fallback."""
        # 1. Try MCP endpoint over HTTP
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = await self._client.post("/mcp", json=request)
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    return {"error": data["error"].get("message", str(data["error"]))}
        except (httpx.HTTPError, Exception) as exc:
            logger.debug("HTTP MCP failed (%s); trying REST/direct fallback", exc)

        # 2. Try REST endpoints over HTTP
        rest_res = await self._try_rest(tool_name, arguments)
        if "error" not in rest_res:
            return rest_res

        # 3. Direct SQLite fallback (zero downtime)
        return self._sqlite_fallback(tool_name, arguments)

    async def _try_rest(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Try RepoIntel REST endpoints."""
        repo_id = arguments.get("repo_id", "")
        mapping = {
            "query_repository": ("POST", "/api/v1/query", {"repo_id": repo_id, "question": arguments.get("question")}),
            "find_findings": ("GET", f"/api/v1/findings/{repo_id}", {"severity": arguments.get("severity")}),
            "get_architecture": ("GET", f"/api/v1/architecture/{repo_id}", {}),
            "get_impact": ("GET", f"/api/v1/impact/{repo_id}", {"symbol": arguments.get("symbol")}),
            "find_symbol_references": ("GET", f"/api/v1/impact/{repo_id}", {"symbol": arguments.get("symbol")}),
            "list_gaps": ("GET", f"/api/v1/repos/{repo_id}/gaps", {}),
            "get_health_score": ("GET", f"/api/v1/repos/{repo_id}/health-score", {}),
        }
        if tool_name not in mapping:
            return {"error": f"Unknown REST tool mapping: {tool_name}"}

        method, path, params = mapping[tool_name]
        try:
            if method == "GET":
                resp = await self._client.get(path, params={k: v for k, v in params.items() if v is not None})
            else:
                resp = await self._client.post(path, json=params)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"REST status {resp.status_code}"}
        except (httpx.HTTPError, Exception) as exc:
            return {"error": f"RepoIntel HTTP request failed: {exc}"}

    def _sqlite_fallback(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Query direct SQLite knowledge base (repointel.db) when HTTP server is offline."""
        try:
            if tool_name in ("get_impact", "find_symbol_references"):
                symbol = arguments.get("symbol", "")
                depth = arguments.get("max_depth", 5)
                limit = arguments.get("limit", 20)
                impact = self._sqlite_client.get_impact_radius(symbol, max_depth=depth, limit=limit)
                arch = self._sqlite_client.get_architecture(repo_id=arguments.get("repo_id"))
                hub_deg = next((h[1] for h in arch.get("top_hubs", []) if h.get("name") == symbol), 0)
                return {
                    "symbol": symbol,
                    "impacted_callers": impact,
                    "caller_count": len(impact),
                    "hub_degree": hub_deg,
                    "source": "repointel.db recursive cte",
                }

            if tool_name == "get_callers":
                symbol = arguments.get("symbol", "")
                callers = self._sqlite_client.get_callers(symbol)
                return {
                    "symbol": symbol,
                    "callers": callers,
                    "caller_count": len(callers),
                    "source": "repointel.db direct sqlite",
                }

            if tool_name == "get_callees":
                symbol = arguments.get("symbol", "")
                callees = self._sqlite_client.get_callees(symbol)
                return {
                    "symbol": symbol,
                    "callees": callees,
                    "callee_count": len(callees),
                    "source": "repointel.db direct sqlite",
                }

            if tool_name == "list_gaps":
                return self._sqlite_client.list_gaps(
                    repo_id=arguments.get("repo_id"),
                    gap_type=arguments.get("gap_type", "all"),
                    limit=arguments.get("limit", 50),
                )

            if tool_name == "get_health_score":
                return self._sqlite_client.get_health_score(repo_id=arguments.get("repo_id"))

            if tool_name in ("query_repository", "search_code"):
                query = arguments.get("question") or arguments.get("query", "")
                limit = arguments.get("limit", 10)
                hits = self._sqlite_client.search_code(query, limit=limit)
                return {"query": query, "hits": hits, "source": "repointel.db direct sqlite"}

            if tool_name in ("find_findings", "run_sast_scan"):
                cat = arguments.get("category") or arguments.get("finding_type")
                finding_type = None if cat == "all" else cat
                findings = self._sqlite_client.get_findings(
                    repo_id=arguments.get("repo_id"),
                    severity=arguments.get("severity"),
                    finding_type=finding_type,
                    limit=arguments.get("limit", 100),
                )
                return {"findings": findings, "count": len(findings), "source": "repointel.db direct sqlite"}

            if tool_name == "get_architecture":
                arch = self._sqlite_client.get_architecture(repo_id=arguments.get("repo_id"))
                return {"architecture": arch, "source": "repointel.db direct sqlite"}

            if tool_name == "search_symbols":
                syms = self._sqlite_client.search_symbols(
                    pattern=arguments.get("pattern", ""),
                    repo_id=arguments.get("repo_id")
                )
                return {"symbols": syms, "count": len(syms), "source": "repointel.db direct sqlite"}

            if tool_name == "check_goal_code_maturity":
                statement = arguments.get("statement") or arguments.get("goal_text", "")
                gid = arguments.get("goal_id", "goal_check")
                mat = self._sqlite_client.check_goal_code_maturity(
                    goal_id=gid,
                    statement=statement,
                    target_kind=arguments.get("target_kind", "goal")
                )
                return mat.__dict__

            return {"error": f"Tool '{tool_name}' not supported in offline SQLite fallback"}
        except Exception as exc:
            return {"error": f"SQLite fallback failed: {exc}"}

    # Convenience shortcuts
    async def search_code(self, repo_id: str, query: str, limit: int = 10) -> Dict[str, Any]:
        return await self.invoke_tool("search_code", {"repo_id": repo_id, "query": query, "limit": limit})

    async def get_callers(self, repo_id: str, symbol: str) -> Dict[str, Any]:
        return await self.invoke_tool("find_symbol_references", {"repo_id": repo_id, "symbol": symbol})

    async def get_findings(self, repo_id: str, severity: Optional[str] = None) -> Dict[str, Any]:
        return await self.invoke_tool("find_findings", {"repo_id": repo_id, "severity": severity})

    async def get_architecture(self, repo_id: str) -> Dict[str, Any]:
        return await self.invoke_tool("get_architecture", {"repo_id": repo_id})


_bridge: Optional[RepoIntelBridge] = None


def get_bridge() -> RepoIntelBridge:
    global _bridge
    if _bridge is None:
        _bridge = RepoIntelBridge()
    return _bridge


async def call_repointel(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call any RepoIntel tool by name."""
    bridge = get_bridge()
    return await bridge.invoke_tool(tool_name, arguments)


async def analyze_code_with_repointel(
    case_id: str,
    repo_id: str,
    symbol_or_query: str,
    analysis_type: str = "impact",
) -> Dict[str, Any]:
    """Ask RepoIntel to analyze code and auto-ingest findings into ICE as evidence.

    analysis_type: impact | callers | findings | architecture | search
    """
    bridge = get_bridge()

    if analysis_type in ("impact", "callers"):
        result = await bridge.get_callers(repo_id, symbol_or_query)
    elif analysis_type == "findings":
        result = await bridge.get_findings(repo_id)
        if "findings" in result and isinstance(result["findings"], list):
            from case_engine.evidence import ingest_evidence as _ingest, _create_source
            src = await _create_source(case_id, "TOOL", "repointel://findings")
            ingested_count = 0
            for f in result["findings"][:20]:
                content = (
                    f"[{f.get('severity', 'info').upper()}] {f.get('rule_id', 'finding')} "
                    f"in {f.get('file_path', '?')}:{f.get('line_start', 0)}\n"
                    f"{f.get('message', '')}"
                )
                await _ingest(
                    case_id=case_id,
                    content=content,
                    source_id=src.source_id,
                    evidence_type="TOOL_RESULT",
                    metadata={"tool": "repointel", "finding": f},
                )
                ingested_count += 1
            return {"ingested": ingested_count, "findings": result["findings"][:20]}
        return result
    elif analysis_type == "architecture":
        result = await bridge.get_architecture(repo_id)
    elif analysis_type == "search":
        result = await bridge.search_code(repo_id, symbol_or_query)
    else:
        return {"error": f"Unknown analysis_type: {analysis_type}"}

    # Ingest summary as TOOL_RESULT evidence
    from case_engine.evidence import ingest_evidence as _ingest, _create_source
    src = await _create_source(case_id, "TOOL", f"repointel://{analysis_type}")
    content = f"RepoIntel {analysis_type} analysis for '{symbol_or_query}':\n\n" + json.dumps(result, indent=2, default=str)[:8000]
    ev = await _ingest(
        case_id=case_id,
        content=content,
        source_id=src.source_id,
        evidence_type="TOOL_RESULT",
        metadata={"tool": "repointel", "analysis_type": analysis_type, "target": symbol_or_query},
    )
    return {"evidence_id": ev.evidence_id, "analysis": result}
