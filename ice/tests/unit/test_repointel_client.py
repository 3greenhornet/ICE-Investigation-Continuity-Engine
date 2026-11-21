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
    assert "ice_repointel_search_code" in tool_names
    assert "ice_repointel_callers" in tool_names
    assert "ice_repointel_architecture" in tool_names
    assert "ice_suggest_next" in tool_names
