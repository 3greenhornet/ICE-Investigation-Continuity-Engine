"""Tests for ICE MCP Server and tool registry."""

import pytest
from app.mcp.server import dispatch_request, PROTOCOL_VERSION
from app.mcp.tools_registry import TOOLS, get_mcp_schemas


@pytest.mark.asyncio
async def test_mcp_server_discover():
    req = {"jsonrpc": "2.0", "id": 1, "method": "server/discover"}
    resp = await dispatch_request(req)
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "ice-investigation-engine"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_mcp_tools_list():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = await dispatch_request(req)
    assert resp is not None
    tools = resp["result"]["tools"]
    assert len(tools) >= 40
    tool_names = {t["name"] for t in tools}
    assert "create_case" in tool_names
    assert "build_ach_matrix" in tool_names
    assert "induce_hypotheses" in tool_names
    assert "ice_create_goal" in tool_names
    assert "ice_verify_code_maturity" in tool_names
    assert "repointel_search_code" in tool_names
    assert "invoke_repointel_tool" in tool_names


@pytest.mark.asyncio
async def test_mcp_tool_schema_structure():
    schemas = get_mcp_schemas()
    for s in schemas:
        assert "name" in s
        assert "description" in s
        assert "inputSchema" in s
        assert s["inputSchema"]["type"] == "object"
        assert "properties" in s["inputSchema"]
        assert isinstance(s["inputSchema"]["required"], list)


@pytest.mark.asyncio
async def test_mcp_tool_call_unknown():
    req = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {"name": "non_existent_tool_123", "arguments": {}},
    }
    resp = await dispatch_request(req)
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_repointel_offline_architecture_call():
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "repointel_get_architecture", "arguments": {}},
    }
    resp = await dispatch_request(req)
    assert resp is not None
    assert "result" in resp
    structured = resp["result"]["structured"]
    assert "architecture" in structured
    assert structured["architecture"]["file_count"] > 0


@pytest.mark.asyncio
async def test_repointel_impact_recursive_cte():
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "repointel_get_impact", "arguments": {"symbol": "get_timestamp", "limit": 5}},
    }
    resp = await dispatch_request(req)
    assert resp is not None
    assert "result" in resp
    impact = resp["result"]["structured"]["impacted_callers"]
    assert len(impact) > 0
    assert "num_call_paths" in impact[0]


@pytest.mark.asyncio
async def test_repointel_list_gaps_and_health():
    req1 = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "repointel_list_gaps", "arguments": {"limit": 10}},
    }
    resp1 = await dispatch_request(req1)
    assert resp1 is not None
    gaps = resp1["result"]["structured"]
    assert gaps["total_gaps"] > 0

    req2 = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "repointel_get_health_score", "arguments": {}},
    }
    resp2 = await dispatch_request(req2)
    assert resp2 is not None
    health = resp2["result"]["structured"]
    assert health["health_score"] >= 0
    assert health["grade"] in ("A", "B", "C", "D")


@pytest.mark.asyncio
async def test_repointel_context_tools():
    # Predictions
    req1 = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "repointel_get_quality_predictions", "arguments": {"min_probability": 0.3, "limit": 5}},
    }
    resp1 = await dispatch_request(req1)
    assert resp1 is not None
    assert resp1["result"]["structured"]["count"] > 0

    # Hotspots
    req2 = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "repointel_get_git_hotspots", "arguments": {"limit": 5}},
    }
    resp2 = await dispatch_request(req2)
    assert resp2 is not None
    assert resp2["result"]["structured"]["count"] > 0

    # Issues
    req3 = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "repointel_get_issues", "arguments": {"limit": 5}},
    }
    resp3 = await dispatch_request(req3)
    assert resp3 is not None
    assert resp3["result"]["structured"]["count"] > 0
