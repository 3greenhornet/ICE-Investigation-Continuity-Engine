"""Integration tests for event-sourced replay (case_engine/replay.py).
Requires DB.

Verifies state reconstruction from case_events, the counterfactual
"without evidence" replay, evidence causal influence, and decision
attribution.

Engine-specific notes (verified against replay.py):
    * replay reconstructs from EVENT PAYLOADS only — hypotheses appear
      via hypothesis_created / hypothesis_version_created, evidence via
      evidence_ingested — so the tests build a small real event log via
      the engine (create_case / create_hypothesis / ingest_evidence) and
      then replay it.
    * compute_decision_attribution reads the decisions table; a Decision
      is produced for real via decide() on a hypothesis-less case
      (rule 1 -> ABSTAIN).
"""
import pytest

from case_engine.case import create_case
from case_engine.decision import decide
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.hypothesis import create_hypothesis, update_hypothesis
from case_engine.replay import (
    CaseState,
    _get_events,
    compute_decision_attribution,
    compute_evidence_causal_influence,
    replay_case,
    replay_without_evidence,
)


async def _seed_case(db_pool):
    """Case + hypothesis + version bump + one evidence item."""
    case = await create_case("Replay case", "objective")
    h = await create_hypothesis(case.case_id, "Pool exhaustion", 0.5)
    await update_hypothesis(h.hypothesis_id, 0.7, "new evidence", "evt_seed")
    source = await _create_source(case.case_id, "HUMAN", None)
    ev = await ingest_evidence(case.case_id, "cpu=92", source.source_id)
    return case, h, ev


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_case_returns_state(db_pool):
    case, h, ev = await _seed_case(db_pool)

    state = await replay_case(case.case_id)
    assert isinstance(state, CaseState)
    assert state.case_id == case.case_id
    assert h.hypothesis_id in state.hypotheses
    # the version bump is replayed: confidence 0.7, not the initial 0.5
    assert state.hypotheses[h.hypothesis_id]["confidence"] == pytest.approx(0.7)
    assert ev.evidence_id in state.evidence_ids
    assert state.event_count >= 4  # created + hypothesis + version + evidence


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_case_event_count_matches(db_pool):
    case, _h, _ev = await _seed_case(db_pool)

    events = await _get_events(case.case_id)
    state = await replay_case(case.case_id)
    assert state.event_count == len(events)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_without_evidence_removes_events(db_pool):
    case, h, ev = await _seed_case(db_pool)

    full = await replay_case(case.case_id)
    without = await replay_without_evidence(case.case_id, ev.evidence_id)

    assert ev.evidence_id in full.evidence_ids
    assert ev.evidence_id not in without.evidence_ids
    # the evidence_ingested event was filtered out entirely
    assert without.event_count == full.event_count - 1
    # unrelated state (the hypothesis) survives the counterfactual
    assert h.hypothesis_id in without.hypotheses


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_case_up_to_seq(db_pool):
    case, h, _ev = await _seed_case(db_pool)

    events = await _get_events(case.case_id)
    # replay only through the hypothesis_version_created event (seq 3)
    truncated = await replay_case(case.case_id, up_to_event_seq=3)
    assert truncated.event_count == 3
    assert h.hypothesis_id in truncated.hypotheses
    assert truncated.evidence_ids == []  # evidence event is seq 4

    _ = events  # (kept for clarity: 4+ events exist in the full log)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compute_evidence_causal_influence(db_pool):
    """Hari ICE improvements pass — the counterfactual now REBUILDS the
    inference from the remaining evidence links instead of replaying the
    recorded confidences (the audit: "That is not a defensible
    counterfactual causal experiment").

    Old expectation (recorded-confidences semantics): removing UNLINKED
    evidence changed nothing -> influence 0.0 "negligible". New semantics:
    the counterfactual reconstructs what the evidence graph alone supports
    — for this seed (hypothesis manually bumped to 0.7, evidence never
    linked) the evidence-only reconstruction is sigmoid(0)=0.5, so the
    influence is |0.7-0.5|=0.2 "moderate". That is the honest reading:
    the recorded belief had no evidential basis, and the counterfactual
    now says so instead of rubber-stamping it."""
    case, _h, ev = await _seed_case(db_pool)

    result = await compute_evidence_causal_influence(case.case_id, ev.evidence_id)
    assert set(result.keys()) == {
        "evidence_id",
        "original_confidence",
        "counterfactual_confidence",
        "causal_influence",
        "interpretation",
    }
    assert result["evidence_id"] == ev.evidence_id
    # recorded 0.7 (manual bump) vs evidence-only reconstruction 0.5
    assert result["original_confidence"] == pytest.approx(0.7)
    assert result["counterfactual_confidence"] == pytest.approx(0.5)
    assert result["causal_influence"] == pytest.approx(0.2)
    assert result["interpretation"] == "moderate"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compute_decision_attribution(db_pool):
    case = await create_case("Attribution case", "objective")
    decision = await decide(case.case_id)  # no hypotheses -> ABSTAIN

    attribution = await compute_decision_attribution(decision.decision_id)
    assert attribution["decision_id"] == decision.decision_id
    assert attribution["case_id"] == case.case_id
    assert attribution["decision_type"] == "ABSTAIN"
    assert "rationale" in attribution
    assert isinstance(attribution["top_factors"], dict)
    assert attribution["top_factors"]["reason"] == "no_hypotheses"

    # unknown decision id -> {} (not an error)
    assert await compute_decision_attribution("dec_missing") == {}