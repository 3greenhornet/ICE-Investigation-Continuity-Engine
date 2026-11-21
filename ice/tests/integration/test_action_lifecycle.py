"""Integration tests for the action lifecycle (case_engine/action.py). Requires DB.

Verifies proposed -> approved/rejected -> executed transitions, the edit
loop (audit gap #10), pending listing, and the Part 2 §4 SEARCH executor
guard rails.

Engine-specific notes (verified against action.py):
    * ``create_action`` requires target/expected_outcome positionally
      (they are Optional in type but not in the signature).
    * execute_action dispatches on action type when no executor is given:
      RETRIEVE (and the other seven no-op types) use NullExecutor and
      complete with empty result_evidence_ids.
    * SEARCH with an http(s) target REQUIRES a scraper key — without one,
      execute_action must transition the action to 'failed', emit
      action_failed, and re-raise (Part 2 §4.3). The no-key guard is what
      is testable deterministically in CI; the with-key path needs live
      network and is deliberately not attempted here (documented
      deviation from the doc outline's scraper-key test).
    * SEARCH with a NON-URL target is an explicit no-op (NullExecutor
      semantics, logged warning) — covered as the key-free counterpart.
"""
import pytest

from case_engine.case import create_case
from case_engine.claim import extract_claims
from case_engine.evidence import ingest_evidence, _create_source
from case_engine.action import (
    approve_action,
    create_action,
    edit_action,
    execute_action,
    get_action,
    get_pending_actions,
    reject_action,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_action_proposed_status(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(
        case.case_id, "RETRIEVE", "fetch deploy logs", "confirm deploy timing"
    )
    assert action.action_id.startswith("act_")
    assert action.status == "proposed"
    assert action.action_type == "RETRIEVE"
    assert action.target == "fetch deploy logs"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_approve_action_transitions(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", "t", "eo")
    approved = await approve_action(action.action_id)
    assert approved.status == "approved"
    # approving twice is rejected (not a proposed action anymore)
    with pytest.raises(ValueError, match="not in 'proposed' state"):
        await approve_action(action.action_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reject_action_transitions(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "SEARCH", "t", "eo")
    rejected = await reject_action(action.action_id, "not worth it")
    assert rejected.status == "rejected"
    assert rejected.completed_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_action_transitions_to_completed(db_pool):
    """Hari ICE improvements pass: RETRIEVE on an evidence-free case is
    UNINFORMATIVE, and an evidence-expecting action type may no longer
    count as 'completed' on an empty result (P1: "an action should not be
    marked as successfully completed merely because an executor returned
    an empty list"). The old assertion (status == 'completed' with zero
    evidence) encoded exactly the behavior the improvements document
    mandates removing.

    Hermeticity note: the query token is UNIQUE PER RUN (uuid prefix).
    The original version used the fixed phrase "fetch logs" and passed
    only on a virgin database: on any populated DB the RETRIEVE executor's
    legitimate cross-case fallback found OTHER cases' log evidence and
    legitimately completed. Worse, a fixed SENTINEL token does not fix
    that either — the test then finds ITS OWN leg-2 evidence from
    PREVIOUS runs (tests do not clean up), so the test only passes the
    first time it is ever run and fails on every rerun. A per-run unique
    token makes leg 1 match nothing anywhere (fresh token, this case has
    no evidence) and leg 2 match only this run's ingested evidence. The
    cross-case path itself is pinned by
    test_execute_retrieve_cross_case_fallback below.
    """
    import uuid

    tok = "zzq" + uuid.uuid4().hex[:10]  # unique across runs and cases
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", tok, "eo")
    await approve_action(action.action_id)
    executed = await execute_action(action.action_id)
    assert executed.status == "failed"  # UNINFORMATIVE on an empty case
    assert executed.result_evidence_ids == []
    assert executed.completed_at is not None
    # ...but with matching evidence in the case the same action completes
    # informatively (the retrieval contract).
    src = await _create_source(case.case_id, "LOG_FILE", None)
    await ingest_evidence(
        case.case_id,
        content=f"{tok} application logs show connection pool saturation "
        "and fetch latency",
        source_id=src.source_id,
    )
    action2 = await create_action(case.case_id, "RETRIEVE", tok, "eo")
    await approve_action(action2.action_id)
    executed2 = await execute_action(action2.action_id)
    assert executed2.status == "completed"
    assert executed2.outcome == "INFORMATIVE_NO_EVIDENCE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_retrieve_cross_case_fallback(db_pool):
    """Pin the cross-case fallback contract the hermetic test above must
    not depend on: RETRIEVE whose query matches no evidence in its own
    case but DOES match another case's material falls back to cross-case
    search, retrieves it, and completes informatively. Both cases are
    created by the test with per-run unique tokens, so this is hermetic
    on any database state and across reruns (it was the behavior that
    silently broke the sibling test on a warm DB)."""
    import uuid

    tok_b = "zzq" + uuid.uuid4().hex[:10]  # unique to this run
    case_a = await create_case("Cross-case A", "objective")
    case_b = await create_case("Cross-case B", "unrelated investigation")
    src_b = await _create_source(case_b.case_id, "LOG_FILE", None)
    await ingest_evidence(
        case_b.case_id,
        content=f"{tok_b} crosscase probe unrelated material found here",
        source_id=src_b.source_id,
    )
    # query token exists in case B's evidence and nowhere in case A
    action = await create_action(case_a.case_id, "RETRIEVE", tok_b, "eo")
    await approve_action(action.action_id)
    executed = await execute_action(action.action_id)
    assert executed.status == "completed"
    assert executed.outcome == "INFORMATIVE_NO_EVIDENCE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_unapproved_raises(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", "t", "eo")
    with pytest.raises(ValueError, match="not 'approved'"):
        await execute_action(action.action_id)
    # the failed attempt must not have moved the action out of proposed
    assert (await get_action(action.action_id)).status == "proposed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edit_action_updates_fields(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(
        case.case_id, "SEARCH", "original target", "original outcome",
        cost_estimate=0.5, risk=0.1,
    )
    edited = await edit_action(
        action.action_id,
        target="edited target",
        expected_outcome="edited outcome",
        cost_estimate=0.2,
        risk=0.0,
    )
    assert edited.target == "edited target"
    assert edited.expected_outcome == "edited outcome"
    assert edited.cost_estimate == 0.2
    assert edited.risk == 0.0
    assert edited.status == "proposed"
    # fields not provided stay untouched
    assert edited.action_type == "SEARCH"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edit_action_partial_update_leaves_rest(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", "keep me", "outcome")
    edited = await edit_action(action.action_id, cost_estimate=0.9)
    assert edited.target == "keep me"          # untouched
    assert edited.cost_estimate == 0.9         # updated


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edit_action_only_in_proposed_state(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", "t", "eo")
    await approve_action(action.action_id)
    with pytest.raises(ValueError, match="can only be edited in 'proposed' state"):
        await edit_action(action.action_id, target="too late")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_edit_action_invalid_floats_raise(db_pool):
    case = await create_case("Action case", "objective")
    action = await create_action(case.case_id, "RETRIEVE", "t", "eo")
    with pytest.raises(ValueError, match="risk must be in"):
        await edit_action(action.action_id, risk=1.5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_pending_actions(db_pool):
    case = await create_case("Action case", "objective")
    pending_a = await create_action(case.case_id, "RETRIEVE", "a", "eo")
    pending_b = await create_action(case.case_id, "SEARCH", "b", "eo")
    approved = await create_action(case.case_id, "INSPECT", "c", "eo")
    await approve_action(approved.action_id)

    pending_ids = [a.action_id for a in await get_pending_actions(case.case_id)]
    assert pending_a.action_id in pending_ids
    assert pending_b.action_id in pending_ids
    assert approved.action_id not in pending_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_search_action_scraper_key_skip_guard(db_pool, monkeypatch):
    """SEARCH on a URL target without a scraper key fails loudly (Part 2 §4.3)."""
    monkeypatch.delenv("SCRAPFLY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("SCRAPER", raising=False)  # default backend: scrapfly

    case = await create_case("Action case", "objective")
    action = await create_action(
        case.case_id, "SEARCH", "https://example.com/status", "scrape the status page"
    )
    await approve_action(action.action_id)

    with pytest.raises(RuntimeError, match="SCRAPFLY_API_KEY"):
        await execute_action(action.action_id)

    failed = await get_action(action.action_id)
    assert failed.status == "failed"

    async with db_pool.acquire() as conn:
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'action_failed'",
            case.case_id,
        )
    assert events == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_search_action_non_url_target_noop(db_pool, monkeypatch):
    """SEARCH with a generator-style (non-URL) target is now an explicit
    UNSUPPORTED outcome (status 'failed' with a remediation note) —
    previously it silently counted as a completed action (Hari ICE
    improvements pass, P1 outcome semantics)."""
    monkeypatch.delenv("SCRAPFLY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    case = await create_case("Action case", "objective")
    action = await create_action(
        case.case_id, "SEARCH", "Search LOG_FILE for new evidence", "diversify sources"
    )
    await approve_action(action.action_id)
    executed = await execute_action(action.action_id)
    assert executed.status == "failed"
    assert executed.result_evidence_ids == []
    assert executed.outcome == "UNSUPPORTED"

@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_compare_produces_derived_evidence(db_pool):
    """Hari ICE fix pass regression: the COMPARE executor previously
    crashed on ingest_evidence() missing 'source_id' — a TypeError that
    only appeared when a COMPARE action was actually EXECUTED (the
    benchmark's --execute-actions run found it live; no suite test had
    ever executed a COMPARE action end-to-end, so the suite stayed green
    while the mechanism was broken in production posture).

    This test pins the full path: two claims with temporal windows, a
    COMPARE action over them, and a derived MODEL_INFERENCE evidence
    record with its own TOOL source, outcome EVIDENCE_PRODUCED.
    """
    import uuid

    tok = "cmp" + uuid.uuid4().hex[:10]
    case = await create_case("Compare case", "objective")
    src = await _create_source(case.case_id, "LOG_FILE", None)
    ev = await ingest_evidence(
        case.case_id,
        content=(
            f"{tok}: first report says latency spiked at 10:00 UTC. "
            f"{tok}: second report says latency was flat until 12:00 UTC. "
            "The deployment happened at 11:00 UTC."
        ),
        source_id=src.source_id,
    )
    claims = await extract_claims(ev.evidence_id)
    assert len(claims) >= 2, f"expected >=2 claims, got {len(claims)}"
    claim_a, claim_b = claims[0], claims[1]

    action = await create_action(
        case.case_id,
        "COMPARE",
        f"{claim_a.claim_id} vs {claim_b.claim_id}",
        "determine temporal ordering of the conflicting reports",
    )
    await approve_action(action.action_id)
    executed = await execute_action(action.action_id)

    assert executed.status == "completed", (
        f"COMPARE crashed instead of completing: "
        f"{getattr(executed, 'result_payload', None)}"
    )
    assert executed.outcome == "EVIDENCE_PRODUCED"
    assert executed.result_evidence_ids, "no derived evidence produced"
    # the derived record exists and is MODEL_INFERENCE
    from case_engine.evidence import get_evidence
    derived = await get_evidence(executed.result_evidence_ids[0])
    assert derived.evidence_type == "MODEL_INFERENCE"
    # its provenance is a TOOL source created by the executor
    pool = db_pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT source_type, source_uri FROM sources "
            "WHERE source_id = $1",
            derived.source_id,
        )
    assert row is not None
    assert row["source_type"] == "TOOL"
    assert (row["source_uri"] or "").startswith("ice://compare/")
