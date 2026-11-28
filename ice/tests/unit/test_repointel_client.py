"""ice/tests/unit/test_repointel_client.py — Unit tests for RepoIntel client & MCP server."""

import pytest
try:
    from app.mcp.server import dispatch_request
except ImportError:
    from mcp_server import dispatch_request
from case_engine.repointel_client import RepoIntelClient, CodeMaturityAssessment


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


def test_repointel_executor_routing():
    from datetime import datetime, timezone
    from case_engine.action import default_executor_for, RepoIntelExecutor, WebSearchExecutor
    from case_engine.schemas import Action

    now = datetime.now(timezone.utc)

    # 1. URL search routes to WebSearchExecutor
    web_action = Action(
        action_id="act_web",
        case_id="case_1",
        action_type="SEARCH",
        target="https://example.com/api/docs",
        expected_outcome="",
        status="approved",
        created_at=now,
    )
    assert isinstance(default_executor_for(web_action), WebSearchExecutor)

    # 2. Natural language / code search routes to RepoIntelExecutor
    code_action = Action(
        action_id="act_code",
        case_id="case_1",
        action_type="SEARCH",
        target="Search for authentication tokens in repository",
        expected_outcome="",
        status="approved",
        created_at=now,
    )
    assert isinstance(default_executor_for(code_action), RepoIntelExecutor)

    # 3. Explicit symbol/repo target routes to RepoIntelExecutor
    symbol_action = Action(
        action_id="act_sym",
        case_id="case_1",
        action_type="INSPECT",
        target="auth_service",
        result_payload={"symbol": "auth_service", "target": "code"},
        expected_outcome="",
        status="approved",
        created_at=now,
    )
    assert isinstance(default_executor_for(symbol_action), RepoIntelExecutor)


def test_thought_model_tz_aware():
    from models.thought import Thought
    t = Thought(id="th_1", content="test thinking", originating_turn=1, last_active_turn=1)
    assert t.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_autonomy_dial():
    from app.mcp.server import dispatch_request
    set_req = {
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {"name": "ice_set_autonomy_level", "arguments": {"level": 5}},
    }
    resp = await dispatch_request(set_req)
    assert resp["result"]["structured"]["autonomy_level"] == 5
    assert "Suggestive" in resp["result"]["structured"]["description"]

    get_req = {
        "jsonrpc": "2.0",
        "id": 202,
        "method": "tools/call",
        "params": {"name": "ice_get_autonomy_level", "arguments": {}},
    }
    resp2 = await dispatch_request(get_req)
    assert resp2["result"]["structured"]["autonomy_level"] == 5


@pytest.mark.asyncio
async def test_repointel_event_sync():
    from app.routes.repointel import repointel_event_sync, RepoIntelEventPayload
    payload = RepoIntelEventPayload(
        event_type="FindingDetected",
        severity="CRITICAL",
        finding_id="SEC_001",
    )
    res = await repointel_event_sync(payload)
    assert res["event_received"] == "FindingDetected"
    assert res["flagged_for_investigation"] is True
    # No case_id -> nothing is persisted, and the response must say so.
    assert res["investigation_opened"] is False
    assert res["persisted_evidence_id"] is None
    assert "hint" in res
