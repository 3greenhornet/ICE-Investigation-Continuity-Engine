"""Unit tests for the AUTONOMY-gated LAB_PROPOSE executor and research-to-Lab loop."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from case_engine import action as action_mod
from case_engine.schemas import Action


def test_default_executor_routes_lab_propose_even_with_repo_id_payload():
    act = Action(
        action_id="act_lab_1",
        case_id="case_1",
        action_type="LAB_PROPOSE",
        approval_class="REVERSIBLE",
        target="Clamp negative pricing inputs cleanly",
        result_payload={"repo_id": "repo_123"},
        created_at=datetime.now(timezone.utc),
    )
    exe = action_mod.default_executor_for(act)
    assert isinstance(exe, action_mod.LabProposeExecutor)


@pytest.mark.asyncio
async def test_lab_propose_executor_respects_autonomy_gate_and_never_applies(monkeypatch):
    act = Action(
        action_id="act_lab_2",
        case_id="case_2",
        action_type="LAB_PROPOSE",
        approval_class="REVERSIBLE",
        target=json.dumps({
            "instruction": "Reduce cross-encoder reranking latency by batching pairs",
            "repo_id": "repo_123",
            "files": [],
        }),
        created_at=datetime.now(timezone.utc),
    )

    prev_level = action_mod.get_autonomy_level()
    try:
        action_mod.set_autonomy_level(0)
        res_blocked = await action_mod.LabProposeExecutor().execute(act)
        assert res_blocked.outcome == action_mod.OUTCOME_UNSUPPORTED

        action_mod.set_autonomy_level(1)
        monkeypatch.setenv("REPOINTEL_URL", "http://127.0.0.1:8001")

        calls: list[tuple[str, dict]] = []

        class FakeResp:
            status_code = 202
            content = b'{"id":"prop_abc","status":"queued","files":["reranker.py"]}'

            def json(self):
                return {"id": "prop_abc", "status": "queued", "files": ["reranker.py"]}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                calls.append((url, json))
                return FakeResp()

        import httpx
        from case_engine import evidence as ev_mod

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

        async def fake_source(*a, **kw):
            return SimpleNamespace(source_id="src_lab_1")

        async def fake_ingest(**kw):
            return SimpleNamespace(evidence_id="ev_lab_1")

        monkeypatch.setattr(ev_mod, "_create_source", fake_source)
        monkeypatch.setattr(ev_mod, "ingest_evidence", fake_ingest)

        res_ok = await action_mod.LabProposeExecutor().execute(act)
        assert res_ok.outcome == action_mod.OUTCOME_EVIDENCE_PRODUCED
        assert res_ok.evidence_ids == ["ev_lab_1"]
        assert len(calls) == 1
        assert calls[0][0].endswith("/api/v1/lab/proposals")
        assert calls[0][1]["files"] == []  # preserves Lab auto-discovery
        assert not any("/apply" in u for u, _ in calls)
    finally:
        action_mod.set_autonomy_level(prev_level)


@pytest.mark.asyncio
async def test_research_to_lab_proposal_creates_reversible_action(monkeypatch):
    captured: dict = {}

    class FakeGap:
        resolved_at = None
        priority = 0.9
        needed_evidence = "Benchmark FlashRank vs CrossEncoder on CPU"

    from case_engine import information_gap, hypothesis, repointel_client

    async def fake_gaps(case_id):
        return [FakeGap()]

    async def fake_hyps(case_id):
        return []

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return Action(
            action_id="act_new_lab",
            case_id=kwargs["case_id"],
            action_type=kwargs["action_type"],
            target=kwargs["target"],
            approval_required=kwargs["approval_required"],
            approval_class=kwargs["approval_class"],
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )

    class FakeClient:
        def __init__(self, repo=None):
            self.is_available = True

        def resolve_repo_id(self):
            return "repo_demo"

        def get_findings(self, **kw):
            return []

    monkeypatch.setattr(information_gap, "list_gaps_for_case", fake_gaps)
    monkeypatch.setattr(hypothesis, "list_hypotheses_for_case", fake_hyps)
    monkeypatch.setattr(repointel_client, "RepoIntelClient", FakeClient)
    monkeypatch.setattr(action_mod, "create_action", fake_create)

    out = await action_mod.research_to_lab_proposal("case_99", repo="demo")
    assert out["action_type"] == "LAB_PROPOSE"
    assert out["approval_class"] == "REVERSIBLE"
    assert captured["approval_required"] is True
    assert "Benchmark FlashRank" in out["instruction"]
