"""Unit tests for the conversational orchestrator and /ask route."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ICE_ROOT = Path(__file__).resolve().parents[2]
if str(ICE_ROOT) not in sys.path:
    sys.path.insert(0, str(ICE_ROOT))


@pytest.mark.unit
def test_is_status_only_query():
    from case_engine.orchestrator import is_status_only_query

    assert is_status_only_query("status") is True
    assert is_status_only_query("What is the current state?") is True
    assert is_status_only_query("Latency spiked after deploy") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrate_input_investigate(monkeypatch):
    from case_engine import orchestrator

    async def fake_get_case(cid):
        return SimpleNamespace(case_id=cid, title="Test")

    async def fake_create_source(*a, **k):
        return SimpleNamespace(source_id="src_1")

    async def fake_ingest(*a, **k):
        return SimpleNamespace(evidence_id="ev_1")

    async def fake_extract(ev_id):
        return [SimpleNamespace(claim_id="cl_1")]

    async def fake_cycle(cid, **kwargs):
        assert kwargs["enable_induction"] is True
        assert kwargs["enable_workspace"] is True
        return {
            "leading_hypothesis": {"hypothesis_id": "h1", "statement": "Pool exhausted", "confidence": 0.72},
            "hypothesis_changes": [],
            "new_contradictions": [],
            "new_gaps": ["g1"],
            "recommended_action": {"action_type": "INSPECT", "target": "pool.py", "rationale": "check size"},
        }

    monkeypatch.setattr("case_engine.case.get_case", fake_get_case)
    monkeypatch.setattr("case_engine.evidence._create_source", fake_create_source)
    monkeypatch.setattr("case_engine.evidence.ingest_evidence", fake_ingest)
    monkeypatch.setattr("case_engine.claim.extract_claims", fake_extract)
    monkeypatch.setattr("case_engine.cycle.run_investigation_cycle", fake_cycle)
    monkeypatch.setattr("case_engine.llm.llm_available", lambda: False)
    monkeypatch.setattr(orchestrator, "_gather_code_context", lambda q, repo_id=None: [])

    res = await orchestrator.orchestrate_input("case_1", "DB connections timing out")
    assert res["mode"] == "investigate"
    assert res["evidence_id"] == "ev_1"
    assert res["claims_extracted"] == 1
    assert res["leading_hypothesis"]["statement"] == "Pool exhausted"
    assert "Pool exhausted" in res["answer"]


@pytest.mark.unit
def test_ask_route_registered():
    from app.routes.reasoning import router

    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/cases/{case_id}/ask" in paths
