"""ice/tests/unit/test_repointel_client.py — Unit tests for RepoIntel client & MCP server."""

import pytest
from case_engine.repointel_client import RepoIntelClient, CodeMaturityAssessment
from mcp_server import dispatch_request


def test_repointel_client_initialization():
    client = RepoIntelClient()
    # Should initialize without crashing even if db path is mocked or auto-detected
    assert hasattr(client, "is_available")
    assert hasattr(client, "search_symbols")
    assert hasattr(client, "search_code")
    assert hasattr(client, "check_goal_code_maturity")
    assert hasattr(client, "explain_finding")
    assert hasattr(client, "get_file_snippet")


def test_file_snippet_traversal_guard():
    client = RepoIntelClient()
    res = client.get_file_snippet("../../Windows/win.ini")
    assert "error" in res
    assert "rejected" in res["error"].lower()


def test_goal_maturity_mock_fallback():
    client = RepoIntelClient(db_path="/nonexistent/path/db.sqlite")
    assessment = client.check_goal_code_maturity("g1", "Implement cache layer")
    assert isinstance(assessment, CodeMaturityAssessment)
    assert assessment.goal_id == "g1"
    assert assessment.suggested_maturity == "no_candidate"
    # search_code on empty should return empty list without crashing
    assert client.search_code("test_query") == []


@pytest.mark.asyncio
async def test_mcp_server_discovery():
    resp = await dispatch_request({"method": "server/discover", "id": 100})
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 100
    assert "tools" in resp["result"]["capabilities"]


@pytest.mark.asyncio
async def test_mcp_server_tools_list():
    resp = await dispatch_request({"method": "tools/list", "id": 101})
    assert resp is not None
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "ice_create_goal" in tool_names
    assert "ice_verify_code_maturity" in tool_names
    assert "ice_advise_next_step" in tool_names
    assert "ice_suggest_perspectives" in tool_names
    assert "repointel_explain_finding" in tool_names
    assert "repointel_get_file_snippet" in tool_names
    assert "ice_repointel_search_code" in tool_names
    assert "ice_repointel_callers" in tool_names
    assert "ice_repointel_architecture" in tool_names
    assert "ice_suggest_next" in tool_names


@pytest.mark.asyncio
async def test_mcp_tool_advise_next_step():
    req = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "ice_advise_next_step",
            "arguments": {"statement": "user authentication", "clarity_level": 2},
        },
    }
    resp = await dispatch_request(req)
    assert resp is not None
    assert resp["id"] == 102
    assert "result" in resp
    assert "structured" in resp["result"]
    advise = resp["result"]["structured"]
    assert "code_maturity" in advise
    assert "recommended_actions" in advise
