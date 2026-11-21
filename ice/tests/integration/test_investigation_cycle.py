"""Integration tests for the investigation cycle (case_engine/cycle.py).
Requires DB.

Verifies run_investigation_cycle end to end: result shape, exact
event-count accounting (merge spec S2), hypothesis lifecycle transitions
wired into the cycle (Part 2 §7), contradiction idempotency across
repeated cycles (Part 2 §9), opt-in auto-execute (Part 2 §4.4), and the
research case_type decision branch (Part 2 §3).

Engine-specific notes (verified against cycle.py):
    * the cycle never calls detect_contradiction itself (detection is a
      claim-plane operation); the idempotency test therefore seeds ONE
      contradiction via the (LLM-patched) detector and asserts repeated
      cycles never duplicate it.
    * select_next_best_action is gated behind the module-level
      ENABLE_NEXT_BEST_EVIDENCE flag (default false), so the
      recommendation/auto-execute tests patch
      case_engine.next_best_action.ENABLE_NEXT_BEST_EVIDENCE to True —
      everything else (generate_actions, approve, execute) is real DB.
"""
import pytest

from case_engine import next_best_action as nba
from case_engine import contradiction as contra_mod
from case_engine.case import create_case
from case_engine.claim import create_claim
from case_engine.contradiction import detect_contradiction, get_active_contradictions
from case_engine.cycle import run_investigation_cycle
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.evidence_graph import EdgeRelation, add_edge
from case_engine.hypothesis import (
    create_hypothesis,
    list_hypotheses_for_case,
    update_hypothesis_lifecycle,
)
from case_engine.information_gap import create_gap


async def _count_events(db_pool, case_id: str) -> int:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1", case_id
        )


async def _count_contradictions(db_pool, case_id: str) -> int:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM contradictions WHERE case_id = $1", case_id
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_returns_result(db_pool):
    case = await create_case("Cycle case", "objective")
    await create_hypothesis(case.case_id, "Pool exhaustion", 0.5)

    result = await run_investigation_cycle(case.case_id)

    assert result["case_id"] == case.case_id
    for key in (
        "leading_hypothesis",
        "hypothesis_changes",
        "new_contradictions",
        "resolved_contradictions",
        "new_gaps",
        "new_actions",
        "recommended_action",
        "decision",
        "lifecycle_changes",
        "auto_executed_action",
        "events_emitted",
        "latency_seconds",
    ):
        assert key in result, f"missing result key: {key}"
    assert isinstance(result["events_emitted"], int)
    assert result["events_emitted"] >= 1  # at least decision_made
    assert result["latency_seconds"] >= 0.0
    assert result["leading_hypothesis"]["hypothesis_id"]
    assert result["decision"]["decision_type"] in ("ACT", "INVESTIGATE", "ABSTAIN")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_increments_events(db_pool):
    case = await create_case("Cycle case", "objective")
    await create_hypothesis(case.case_id, "Pool exhaustion", 0.5)

    before = await _count_events(db_pool, case.case_id)
    result = await run_investigation_cycle(case.case_id)
    after = await _count_events(db_pool, case.case_id)

    # S2: events_emitted counts exactly this cycle's events
    assert result["events_emitted"] == after - before
    assert after > before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_twice_no_duplicate_contradictions(
    db_pool, monkeypatch
):
    """Part 2 §9 at the cycle level: re-running the cycle must not
    duplicate an existing contradiction (claim_pair_key + pre-check)."""
    case = await create_case("Cycle case", "objective")
    a = await create_claim(case.case_id, "latency rose", "MEASUREMENT")
    b = await create_claim(case.case_id, "latency fell", "MEASUREMENT")

    async def fake_check(_a, _b):
        return "contradiction", 0.9

    monkeypatch.setattr(contra_mod, "llm_implication_check", fake_check)
    contra = await detect_contradiction(a.claim_id, b.claim_id)
    assert contra is not None
    assert await _count_contradictions(db_pool, case.case_id) == 1

    await run_investigation_cycle(case.case_id)
    await run_investigation_cycle(case.case_id)

    assert await _count_contradictions(db_pool, case.case_id) == 1
    assert len(await get_active_contradictions(case.case_id)) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_lifecycle_transitions(db_pool):
    """Part 2 §7 wired into the cycle: enough supporting evidence lifts a
    PROPOSED hypothesis past 0.7 -> the cycle transitions it to SUPPORTED
    (recompute runs BEFORE update_hypothesis_lifecycle)."""
    case = await create_case("Cycle case", "objective")
    h = await create_hypothesis(case.case_id, "Pool exhaustion", 0.5)
    assert h.status == "PROPOSED"

    source = await _create_source(case.case_id, "HUMAN", None)
    ev1 = await ingest_evidence(case.case_id, "cpu=92", source.source_id)
    ev2 = await ingest_evidence(case.case_id, "memory usage 4gb", source.source_id)
    # two direct evidence -> hypothesis SUPPORTS edges (weight 1.0);
    # MEASUREMENT quality 0.95 each -> raw 1.9 -> sigmoid ~= 0.87 >= 0.7
    for ev in (ev1, ev2):
        await add_edge(
            case.case_id,
            ev.evidence_id,
            "evidence",
            h.hypothesis_id,
            "hypothesis",
            EdgeRelation.SUPPORTS,
            weight=1.0,
        )

    result = await run_investigation_cycle(case.case_id)

    transitions = {t["hypothesis_id"]: t["new_status"] for t in result["lifecycle_changes"]}
    assert transitions.get(h.hypothesis_id) == "SUPPORTED"

    reloaded = [x for x in await list_hypotheses_for_case(case.case_id)
                if x.hypothesis_id == h.hypothesis_id][0]
    assert reloaded.status == "SUPPORTED"
    # the recompute also produced a confidence change entry
    assert any(
        c["hypothesis_id"] == h.hypothesis_id for c in result["hypothesis_changes"]
    )
    assert result["leading_hypothesis"]["confidence"] >= 0.7


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_hypothesis_lifecycle_direct(db_pool):
    """The lifecycle function itself (used by the cycle), on a clean case:
    0.8-confidence PROPOSED hypothesis -> SUPPORTED; a 0.2-confidence
    SUPPORTED hypothesis -> REMAINS_OPEN."""
    case = await create_case("Cycle case", "objective")

    strong = await create_hypothesis(case.case_id, "Strong hypothesis", 0.8)
    new_status = await update_hypothesis_lifecycle(strong.hypothesis_id, current_turn=1)
    assert new_status == "SUPPORTED"

    weak = await create_hypothesis(case.case_id, "Weak hypothesis", 0.2)
    from case_engine.hypothesis import set_hypothesis_status

    await set_hypothesis_status(weak.hypothesis_id, "SUPPORTED", "manually supported")
    new_status = await update_hypothesis_lifecycle(weak.hypothesis_id, current_turn=2)
    assert new_status == "REMAINS_OPEN"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_auto_execute(db_pool, monkeypatch):
    """Part 2 §4.4: with enable_auto_execute the top-priority action is
    approved + executed inside the cycle."""
    monkeypatch.setattr(nba, "ENABLE_NEXT_BEST_EVIDENCE", True)

    case = await create_case("Cycle case", "objective")
    # a >= 0.3-priority gap makes generate_actions propose a RETRIEVE
    await create_gap(
        case.case_id,
        "connection pool metrics",
        "discriminate the pool hypothesis",
        [],
        priority=0.8,
    )
    # Hari ICE improvements pass: RETRIEVE now has a REAL executor — an
    # empty case yields UNINFORMATIVE (status 'failed'), so seed matching
    # evidence for the retrieval to be informative and complete.
    from case_engine.evidence import _create_source, ingest_evidence

    src_row = await _create_source(case.case_id, "LOG_FILE", None)
    await ingest_evidence(
        case.case_id,
        content="connection pool metrics: active=20 max=20 wait_ms=4800",
        source_id=src_row.source_id,
    )

    result = await run_investigation_cycle(
        case.case_id, enable_auto_execute=True
    )

    auto = result["auto_executed_action"]
    assert auto is not None, "expected the top action to be auto-executed"
    assert auto["status"] == "completed"
    assert auto["action_type"] == "RETRIEVE"
    # without auto-execute the same cycle leaves it unexecuted (default)
    result2 = await run_investigation_cycle(case.case_id)
    assert result2["auto_executed_action"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_research_case(db_pool):
    """Part 2 §3: a research-type case decides via the synthesis ladder —
    with no hypotheses it must INVESTIGATE (no_hypotheses)."""
    case = await create_case("Cycle case research", "objective", case_type="research")

    result = await run_investigation_cycle(case.case_id)

    assert result["decision"]["decision_type"] == "INVESTIGATE"
    assert result["decision"]["top_factors"]["reason"] == "no_hypotheses"
    assert result["decision"]["top_factors"]["case_type"] == "research"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_recommends_action_when_enabled(db_pool, monkeypatch):
    monkeypatch.setattr(nba, "ENABLE_NEXT_BEST_EVIDENCE", True)
    case = await create_case("Cycle case", "objective")
    await create_hypothesis(case.case_id, "Pool exhaustion", 0.4)
    await create_gap(
        case.case_id,
        "pool saturation metrics",
        "discriminate the pool hypothesis",
        [],
        priority=0.8,
    )

    result = await run_investigation_cycle(case.case_id)
    assert result["recommended_action"] is not None
    assert result["recommended_action"]["action_type"] == "RETRIEVE"
    assert 0.0 <= result["recommended_action"]["priority"] <= 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_investigation_cycle_missing_case_raises(db_pool):
    from case_engine.exceptions import CaseNotFoundError

    with pytest.raises(CaseNotFoundError):
        await run_investigation_cycle("case_does_not_exist_000")