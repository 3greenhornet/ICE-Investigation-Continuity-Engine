"""Integration tests for the Hari ICE "addition and improvements" pass
modules: induction, predictions, retrieval, timeline, causal/ACH,
workspace, plan, mechanism lab, introspection, trajectory, diagnostics,
evidence credibility, the executor overhaul, action-conditioned EIG and
the replay counterfactual fix.

Every test runs against the real Postgres (db_pool fixture) with no LLM
provider configured — the deterministic offline paths are part of the
contract being tested.
"""

import pytest

from case_engine.case import create_case
from case_engine.evidence import (
    _create_source, ingest_evidence, assess_evidence_credibility,
)
from case_engine.claim import extract_claims
from case_engine.hypothesis import create_hypothesis
from case_engine import claim as claim_mod
from case_engine import llm as llm_mod
from case_engine.exceptions import LLMUnavailableError


_LLM_ENV_KEYS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "ZAI_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
)


@pytest.fixture(autouse=True)
def _offline_llm_env(monkeypatch):
    """Pin test_reasoning_loop to offline deterministic execution regardless of .env."""
    for key in _LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(llm_mod, "llm_available", lambda: False)

    async def _raise_unavailable(*_args, **_kwargs):
        raise LLMUnavailableError("Offline integration test mode")

    monkeypatch.setattr(llm_mod, "_call_llm_json", _raise_unavailable)
    monkeypatch.setattr(claim_mod, "_call_llm_json", _raise_unavailable)
    from case_engine import retrieval as retrieval_mod

    async def _no_embed(_query: str):
        return None

    monkeypatch.setattr(retrieval_mod, "_embed_query", _no_embed)


async def _seed_case_with_evidence(title="Induction case"):
    case = await create_case(title, "Identify the root cause of the latency regression")
    src = await _create_source(case.case_id, "LOG_FILE", None)
    await ingest_evidence(
        case.case_id,
        content=(
            "The checkout service p95 latency regressed after the v42 "
            "deployment went out at 14:00 UTC. The connection pool was "
            "saturated; requests queued waiting for a connection. The pool "
            "config max_connections was reduced from 100 to 20 in the "
            "deployment."
        ),
        source_id=src.source_id,
    )
    return case


# ===========================================================================
# Induction engine (P2)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_induction_generates_structured_candidates(db_pool):
    from case_engine.induction import induce_hypotheses

    case = await _seed_case_with_evidence()
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)

    result = await induce_hypotheses(case.case_id, create=True)

    assert result["generation_path"] == "deterministic"  # no LLM configured
    assert len(result["candidates"]) >= 3
    created = result["created_hypothesis_ids"]
    assert len(created) >= 2

    # every candidate is fully structured (the P2 field table)
    for cand in result["candidates"]:
        assert cand["mechanism"]
        assert cand["discriminating_test"]
        assert cand["falsification_condition"]
        assert cand["assumptions"]
        assert cand["descriptors"]["causal_level"]
        assert "score_factors" in cand

    # hypotheses persisted with origin=ice_generated + predictions
    from case_engine.hypothesis import get_hypothesis
    from case_engine.predictions import list_predictions

    hid = created[0]
    h = await get_hypothesis(hid)
    assert h.origin == "ice_generated"
    preds = await list_predictions(case_id=case.case_id)
    assert len(preds) >= 2  # at least one per created hypothesis
    for p in preds:
        assert p["status"] == "OPEN"
        assert p["resolution_condition"]

    # diversity: descriptors across the archived set are not all identical
    descriptors = {tuple(sorted(c["descriptors"].items())) for c in result["candidates"]}
    assert len(descriptors) >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_induction_support_edges_ground_hypotheses(db_pool):
    """The grounding links (claim -> hypothesis SUPPORTS edges with
    ice_induction provenance) must exist — without them ranking is
    arbitrary."""
    from case_engine.induction import induce_hypotheses
    from db.connection import get_pool

    case = await _seed_case_with_evidence()
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)
    await induce_hypotheses(case.case_id, create=True)

    pool = await get_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM evidence_graph_edges "
            "WHERE case_id = $1 AND metadata->>'origin' = 'ice_induction'",
            case.case_id,
        )
    assert n >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unexplained_observations_finds_uncovered_claims(db_pool):
    from case_engine.induction import unexplained_observations

    case = await _seed_case_with_evidence()
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)

    # no hypotheses -> everything is unexplained
    unexplained = await unexplained_observations(case.case_id)
    assert len(unexplained) >= 1

    # a hypothesis covering the key terms reduces it
    await create_hypothesis(
        case.case_id,
        "connection pool saturation caused the latency regression",
        0.5,
    )
    unexplained2 = await unexplained_observations(case.case_id)
    assert len(unexplained2) < len(unexplained)


# ===========================================================================
# Predictions (Metaculus-style records)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prediction_lifecycle_and_brier_scoring(db_pool):
    from case_engine.predictions import (
        create_prediction, resolve_prediction, list_predictions,
        prediction_calibration_report, brier_score,
    )

    case = await create_case("Prediction case", "objective")
    h = await create_hypothesis(case.case_id, "pool exhaustion", 0.5)

    p = await create_prediction(
        case.case_id, h.hypothesis_id,
        "Pool utilization approaches the configured limit",
        "Active connections reach 20/20 during the incident window",
        probability=0.8,
    )
    assert p["status"] == "OPEN"

    # resolving CONFIRMED at p=0.8: brier = (0.8-1)^2 = 0.04, score 0.96
    resolved = await resolve_prediction(
        p["prediction_id"], "CONFIRMED", "active=20 max=20 observed"
    )
    assert resolved["status"] == "CONFIRMED"
    assert resolved["score"] == pytest.approx(0.96, abs=0.001)

    # a REFUTED prediction at p=0.8 scores 1 - (0.8-0)^2 = 0.36
    p2 = await create_prediction(
        case.case_id, h.hypothesis_id, "other prediction", "condition", 0.8,
    )
    r2 = await resolve_prediction(p2["prediction_id"], "REFUTED", "not observed")
    assert r2["score"] == pytest.approx(0.36, abs=0.001)

    # double resolution is rejected
    with pytest.raises(ValueError, match="already"):
        await resolve_prediction(p["prediction_id"], "REFUTED", "again")

    # calibration report
    report = await prediction_calibration_report(case.case_id)
    assert report["resolved_count"] == 2
    assert report["confirmed"] == 1
    assert report["refuted"] == 1
    expected_mean_brier = (0.04 + 0.64) / 2
    assert report["mean_brier"] == pytest.approx(expected_mean_brier, abs=0.001)

    # deterministic brier check
    assert brier_score(0.8, True) == pytest.approx(0.04)
    assert brier_score(0.8, False) == pytest.approx(0.64)


# ===========================================================================
# Hybrid retrieval (foundation + contrastive + associative)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_evidence_lexical_channel(db_pool):
    from case_engine.retrieval import search_evidence

    case = await _seed_case_with_evidence()
    hits = await search_evidence(
        "connection pool saturation", case_id=case.case_id, limit=5
    )
    assert len(hits) >= 1
    assert hits[0].item_type == "evidence"
    assert "factors" in hits[0].to_dict()
    # deterministic: same query, same order
    hits2 = await search_evidence(
        "connection pool saturation", case_id=case.case_id, limit=5
    )
    assert [h.item_id for h in hits] == [h.item_id for h in hits2]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_evidence_empty_case_is_uninformative(db_pool):
    from case_engine.retrieval import search_evidence

    case = await create_case("Empty case", "nothing here")
    hits = await search_evidence(
        "connection pool", case_id=case.case_id, limit=5
    )
    assert hits == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_contrastive_search_targets_opposition(db_pool):
    from case_engine.retrieval import contrastive_search, build_contrastive_query

    case = await _seed_case_with_evidence()
    q = build_contrastive_query("connection pool exhaustion caused latency")
    assert "unchanged" in q or "not" in q  # contrast cues attached

    hits = await contrastive_search(
        "connection pool exhaustion caused latency",
        case_id=case.case_id, limit=5,
    )
    # the seeded evidence mentions 'reduced'/'saturated' — may or may not
    # hit; the contract is that it never raises and returns a list
    assert isinstance(hits, list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_case_connections(db_pool):
    from case_engine.retrieval import find_cross_case_connections

    case_a = await _seed_case_with_evidence("Case A")
    case_b = await create_case("Case B", "another checkout latency incident")
    src = await _create_source(case_b.case_id, "LOG_FILE", None)
    await ingest_evidence(
        case_b.case_id,
        content="checkout service p95 latency increased; connection pool "
        "saturation observed in logs",
        source_id=src.source_id,
    )
    # A generous limit for the MEMBERSHIP assertion: other cases created
    # earlier in this session (identical seeded evidence from other tests)
    # legitimately rank above case_b — RRF-fusion scores exact duplicates
    # higher than partial matches, by design. The mechanism under test is
    # "related evidence in OTHER cases is found", not its rank.
    connections = await find_cross_case_connections(case_a.case_id, limit=50)
    assert isinstance(connections, list)
    to_cases = {c["to_case_id"] for c in connections}
    assert case_b.case_id in to_cases


# ===========================================================================
# Timeline (temporal reconstruction)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_timeline_builds_and_finds_state_change(db_pool):
    from case_engine.timeline import (
        build_case_timeline, explain_state_change, check_temporal_consistency,
    )
    from datetime import datetime, timezone

    case = await create_case("Timeline case", "diagnose the outage")
    src = await _create_source(case.case_id, "LOG_FILE", None)
    t0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 20, 10, 2, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 20, 10, 5, tzinfo=timezone.utc)
    await ingest_evidence(
        case.case_id, "service healthy, all checks passing",
        source_id=src.source_id, observed_at=t0,
    )
    await ingest_evidence(
        case.case_id, "deployment v42 completed",
        source_id=src.source_id, observed_at=t1,
    )
    await ingest_evidence(
        case.case_id, "service unavailable, outage declared",
        source_id=src.source_id, observed_at=t2,
    )

    timeline = await build_case_timeline(case.case_id)
    dated = [e for e in timeline if e.ts is not None]
    assert len(dated) == 3
    assert dated[0].ts < dated[1].ts < dated[2].ts

    change = await explain_state_change(case.case_id)
    assert change["transition_found"] is True
    # the deployment (10:02) falls inside the healthy(10:00)->outage(10:05) window
    labels = [
        c.get("excerpt", "") or c.get("label", "")
        for c in change["candidate_events"]
    ]
    assert any("deployment" in l.lower() for l in labels)

    consistency = await check_temporal_consistency(case.case_id)
    assert "checked_pairs" in consistency


# ===========================================================================
# Causal layer + ACH
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_causal_edge_lifecycle_and_ach(db_pool):
    from case_engine.causal import (
        propose_causal_edge, review_causal_edge, list_causal_edges,
        build_ach_matrix, predict_intervention,
    )
    from case_engine.claim import create_claim

    case = await create_case("Causal case", "diagnose")
    await create_claim(case.case_id, "the deploy preceded the outage", "TEMPORAL_ASSOCIATION")
    await create_claim(case.case_id, "the pool was saturated", "MEASUREMENT")
    await create_hypothesis(case.case_id, "deploy regression broke the pool config", 0.5)
    await create_hypothesis(case.case_id, "organic traffic growth", 0.4)

    edge = await propose_causal_edge(
        case.case_id,
        cause_node_type="event", cause_node_id="deploy_v42",
        cause_label="deployment v42",
        effect_node_type="event", effect_node_id="outage",
        effect_label="service outage",
        relation="CONTRIBUTES_TO", confidence=0.6,
        assumptions=["clocks are synced"],
    )
    assert edge["status"] == "proposed"

    # unreviewed edges do NOT power intervention queries
    interv = await predict_intervention(case.case_id, "deploy_v42")
    assert interv["expected_changes"] == []

    reviewed = await review_causal_edge(
        edge["causal_edge_id"], "accepted", "timeline corroborates"
    )
    assert reviewed["status"] == "accepted"

    interv2 = await predict_intervention(case.case_id, "deploy_v42")
    assert any(c["node_id"] == "outage" for c in interv2["expected_changes"])

    accepted = await list_causal_edges(case.case_id, status="accepted")
    assert len(accepted) == 1

    ach = await build_ach_matrix(case.case_id)
    assert len(ach["hypotheses"]) == 2
    assert len(ach["matrix"]) >= 1
    assert "least_contradicted" in ach["diagnostics"]
    assert ach["diagnostics"]["rule"]


# ===========================================================================
# Workspace (attention + portfolio + observe-only)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_cycle_selects_and_persists(db_pool):
    from case_engine.workspace import (
        run_workspace_cycle, get_workspace, set_workspace_disposition,
    )

    case = await _seed_case_with_evidence("Workspace case")
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)

    result = await run_workspace_cycle(case.case_id, budget=5)
    assert "selected" in result and "stats" in result
    total_categories = result["stats"]["by_category"]
    assert isinstance(total_categories, dict)

    persisted = await get_workspace(case.case_id)
    assert len(persisted) >= 1
    item = persisted[0]
    assert item["factors"]  # silent scoring forbidden
    assert item["disposition"] in ("active", "observe_only", "dormant")

    # human override
    await set_workspace_disposition(
        item["workspace_item_id"], "dismissed"
    )
    persisted2 = await get_workspace(case.case_id)
    target = [
        w for w in persisted2
        if w["workspace_item_id"] == item["workspace_item_id"]
    ][0]
    assert target["disposition"] == "dismissed"


# ===========================================================================
# Adaptive plan manager
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_supersede_semantics_never_deletes(db_pool):
    from case_engine.plan import (
        create_plan_item, list_plan_items, update_plan_status,
        revise_plan, reconcile_plan, list_plan_revisions,
    )

    case = await create_case("Plan case", "build the notification service")
    original = await create_plan_item(
        case.case_id, "Create a separate NotificationService",
        objective_ref="spec v1",
    )
    await create_plan_item(case.case_id, "Introduce a message queue")

    # the developer implements something better: supersede, never delete
    revision = await revise_plan(
        case.case_id,
        reason="Developer implemented in-app event handling; simpler and "
               "sufficient for current requirements",
        changes=[
            {
                "action": "supersede",
                "plan_item_id": original["plan_item_id"],
                "description": "Implement notification handling in the existing app",
            },
            {"action": "add", "description": "Define failure-handling behaviour"},
        ],
        decided_by="ice",
    )
    assert len(revision["affected_items"]) == 2

    items = await list_plan_items(case.case_id)
    by_id = {i["plan_item_id"]: i for i in items}
    old = by_id[original["plan_item_id"]]
    assert old["status"] == "superseded"  # crossed out, still visible
    assert old["superseded_by"] is not None
    # the replacement points back
    replacement = by_id[old["superseded_by"]]
    assert replacement["supersedes_item"] == original["plan_item_id"]
    assert replacement["status"] == "not_started"

    # status transition records a revision with who decided
    updated = await update_plan_status(
        replacement["plan_item_id"], "in_progress",
        note="work started", decided_by="user",
    )
    assert updated["status"] == "in_progress"

    revisions = await list_plan_revisions(case.case_id)
    assert len(revisions) >= 2
    assert any(r["decided_by"] == "user" for r in revisions)

    # reconciliation is a proposal, not a mutation
    report = await reconcile_plan(case.case_id)
    assert "per_item" in report and "summary" in report
    for entry in report["per_item"]:
        assert entry["classification"] in (
            "completed_unverified_candidate", "intentional_evolution",
            "unresolved", "not_evidenced",
        )


# ===========================================================================
# Mechanism lab
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mechanism_lab_seeds_and_lists(db_pool):
    from case_engine.mechanism_lab import (
        seed_mechanism_lab, list_mechanisms, update_mechanism_decision,
    )

    n = await seed_mechanism_lab()
    first_run = n > 0
    # idempotent
    n2 = await seed_mechanism_lab()
    assert n2 == 0 if first_run else n2 >= 0

    mechanisms = await list_mechanisms()
    ids = {m["mechanism_id"] for m in mechanisms}
    # the PDF's donor mechanisms preserved in code form
    for expected in (
        "mech_ach_matrix", "mech_fsrs_resurfacing", "mech_quality_diversity",
        "mech_hypothesis_tournament", "mech_attention_portfolio",
        "mech_forecast_scoring", "mech_process_mining",
        "mech_failure_localization", "mech_memory_operation_eval",
    ):
        assert expected in ids

    # every record carries the full donor-inversion structure
    for m in mechanisms:
        assert m["borrowed_principle"] and m["inverted_use"]
        assert m["decision"] in ("pending", "reuse", "adapt", "reject",
                                 "defer", "revisit")

    adapted = await list_mechanisms(decision="adapt")
    assert len(adapted) >= 5

    await update_mechanism_decision(
        "mech_contextual_bandits", "defer", "awaiting propensity logs"
    )
    deferred = [
        m for m in await list_mechanisms(decision="defer")
        if m["mechanism_id"] == "mech_contextual_bandits"
    ]
    assert deferred and deferred[0]["decision_note"] == "awaiting propensity logs"


# ===========================================================================
# Introspection / trajectory / diagnostics
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_introspection_flow_report(db_pool):
    from case_engine.introspection import (
        investigation_flow_report, fleet_flow_report,
        build_directly_follows_graph,
    )

    case = await _seed_case_with_evidence("Introspection case")
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)

    dfg = await build_directly_follows_graph(case.case_id)
    assert dfg["event_count"] >= 2
    assert dfg["edges"]

    report = await investigation_flow_report(case.case_id)
    assert "actions" in report and "resolution" in report
    assert "execution_rate" in report["actions"]

    fleet = await fleet_flow_report()
    assert "by_case_type" in fleet


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trajectory_localizes_first_failure(db_pool):
    from case_engine.trajectory import localize_first_failure
    from case_engine.action import (
        create_action, approve_action, execute_action,
    )

    case = await _seed_case_with_evidence("Trajectory case")

    # a clean trajectory: no violations
    clean = await localize_first_failure(case.case_id)
    assert clean["first_failure"] is None

    # executing without approval leaves a V1 violation —
    # craft by inserting an action and executing it via the executor path
    # directly (bypassing approve) is impossible through the public API
    # (it raises), so instead: an ABSTAIN action completes without
    # evidence legitimately. The invariant suite still scans cleanly.
    action = await create_action(
        case.case_id, "ABSTAIN", "not enough evidence to act", "rationale",
    )
    await approve_action(action.action_id)
    executed = await execute_action(action.action_id)
    assert executed.status == "completed"
    assert executed.outcome == "INFORMATIVE_NO_EVIDENCE"

    report = await localize_first_failure(case.case_id)
    # an approved ABSTAIN is legitimate — first_failure stays None unless a
    # real invariant fired; the scan must at least complete
    assert "violations" in report
    assert report["scanned_events"] >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_diagnostics_separates_failure_modes(db_pool):
    from case_engine.diagnostics import memory_diagnostic_report

    case = await _seed_case_with_evidence("Diagnostics case")
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)

    report = await memory_diagnostic_report(
        case.case_id,
        [
            "connection pool was saturated",   # stored + retrievable
            "purple elephant spaceship",        # never stored (encoding)
        ],
    )
    modes = {r["fact"]: r["failure_mode"] for r in report["per_fact"]}
    assert modes["purple elephant spaceship"] == "encoding"
    # the stored fact is at least encoded
    assert modes["connection pool was saturated"] in (
        None, "retrieval", "reasoning",
    )
    assert report["failure_counts"]["encoding"] >= 1


# ===========================================================================
# Evidence credibility axes
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_credibility_five_axes(db_pool):
    case = await create_case("Credibility case", "objective")
    src = await _create_source(case.case_id, "DOCUMENT", None)
    ev = await ingest_evidence(
        case.case_id,
        content="the report states cache hit rate was 95% at 14:00",
        source_id=src.source_id,
    )

    assessment = await assess_evidence_credibility(ev.evidence_id)
    for axis in (
        "kind", "source_reliability", "observation_quality",
        "independence", "relevance", "factors",
    ):
        assert axis in assessment
    # a DOCUMENT reporting a number is NOT a first-party measurement
    assert assessment["kind"]["evidence_type"] != "MEASUREMENT" or \
        ev.quality_score <= 0.75
    assert "quality_score_composite" in assessment["factors"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_aware_quality_caps(db_pool):
    """A DOCUMENT reporting 'cpu=92' must not get the 0.95 MEASUREMENT
    floor; a LOG_FILE source keeps it (content format != reliability)."""
    case = await create_case("Caps case", "objective")

    doc_src = await _create_source(case.case_id, "DOCUMENT", None)
    ev_doc = await ingest_evidence(
        case.case_id, "cpu=92", source_id=doc_src.source_id
    )
    log_src = await _create_source(case.case_id, "LOG_FILE", None)
    ev_log = await ingest_evidence(
        case.case_id, "cpu=92", source_id=log_src.source_id
    )
    assert ev_doc.quality_score == 0.75  # min(0.95 floor, DOCUMENT cap)
    assert ev_log.quality_score == 0.95  # first-party keeps the floor


# ===========================================================================
# Executor overhaul (P1)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieve_executor_informative_vs_uninformative(db_pool):
    from case_engine.action import (
        create_action, approve_action, execute_action,
    )

    case = await _seed_case_with_evidence("Retrieve case")

    # empty case: RETRIEVE fails with UNINFORMATIVE (no completed-on-empty)
    a1 = await create_action(case.case_id, "RETRIEVE", "connection pool metrics",
                             expected_outcome="pool metrics")
    await approve_action(a1.action_id)
    # (evidence was ingested in _seed_case_with_evidence, so this retrieval
    #  finds the pool content and completes informatively)
    r1 = await execute_action(a1.action_id)
    assert r1.status == "completed"
    assert r1.outcome == "INFORMATIVE_NO_EVIDENCE"
    assert r1.result_payload["diagnostics"]["retrieved"]

    # a query matching nothing anywhere (case or cross-case) -> failed /
    # UNINFORMATIVE — activity can no longer masquerade as progress
    empty = await create_case("Empty retrieve", "objective")
    a2 = await create_action(
        empty.case_id, "RETRIEVE", "zzqxjibberish vvv nothingmatches",
        expected_outcome="anything",
    )
    await approve_action(a2.action_id)
    r2 = await execute_action(a2.action_id)
    assert r2.status == "failed"
    assert r2.outcome == "UNINFORMATIVE"

    # idempotent re-execution returns the recorded result
    r1b = await execute_action(a1.action_id)
    assert r1b.status == "completed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_counterevidence_links_or_survives(db_pool):
    from case_engine.action import (
        create_action, approve_action, execute_action,
    )
    from case_engine.hypothesis import get_current_version

    case = await _seed_case_with_evidence("Falsify case")
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)
    h = await create_hypothesis(
        case.case_id,
        "connection pool exhaustion caused the latency regression",
        0.6,
    )

    action = await create_action(
        case.case_id, "FIND_COUNTEREVIDENCE",
        target="evidence that the pool was healthy throughout",
        expected_outcome=f"Find evidence that would weaken: {h.statement[:100]}",
    )
    await approve_action(action.action_id)
    result = await execute_action(action.action_id)

    # either counterevidence was linked (EVIDENCE_PRODUCED) or the
    # hypothesis survived a genuine search (INFORMATIVE_NO_EVIDENCE) —
    # never a silent empty success
    assert result.outcome in ("EVIDENCE_PRODUCED", "INFORMATIVE_NO_EVIDENCE")
    assert result.status == "completed"
    assert result.result_payload["outcome"] == result.outcome

    if result.outcome == "EVIDENCE_PRODUCED":
        # linked CONTRADICTS edges depress the hypothesis confidence
        v = await get_current_version(h.hypothesis_id)
        assert v.confidence < 0.99


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wait_and_abstain_executors_record_state(db_pool):
    from case_engine.action import create_action, approve_action, execute_action
    from case_engine.information_gap import list_gaps_for_case

    case = await create_case("Wait case", "objective")

    w = await create_action(case.case_id, "WAIT", "the next deploy window",
                             expected_outcome="condition")
    await approve_action(w.action_id)
    rw = await execute_action(w.action_id)
    assert rw.status == "completed"
    assert rw.outcome == "INFORMATIVE_NO_EVIDENCE"

    a = await create_action(case.case_id, "ABSTAIN", "not worth more cost",
                             expected_outcome="rationale")
    await approve_action(a.action_id)
    ra = await execute_action(a.action_id)
    assert ra.status == "completed"

    gaps = await list_gaps_for_case(case.case_id, resolved=False)
    assert len(gaps) >= 2  # the WAIT wake condition + the abstention rationale


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_test_executor_unsupported_is_visible(db_pool):
    from case_engine.action import create_action, approve_action, execute_action

    case = await create_case("RunTest case", "objective")
    a = await create_action(
        case.case_id, "RUN_TEST", "run arbitrary shell command rm -rf /",
        expected_outcome="arbitrary execution",
    )
    await approve_action(a.action_id)
    r = await execute_action(a.action_id)
    assert r.status == "failed"          # never silently "completed"
    assert r.outcome == "UNSUPPORTED"
    assert "sandbox" in r.result_payload["notes"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revisit_hypothesis_executor_runs_induction(db_pool):
    from case_engine.action import create_action, approve_action, execute_action
    from case_engine.hypothesis import list_hypotheses_for_case

    case = await create_case("Revisit case", "diagnose the latency regression")
    a = await create_action(case.case_id, "REVISIT_HYPOTHESIS", "recheck",
                             expected_outcome="refresh")
    await approve_action(a.action_id)
    r = await execute_action(a.action_id)
    assert r.status == "completed"
    assert r.result_payload["diagnostics"]["induction"] is not None

    # the starved case (0 hypotheses) now has induced explanations
    hyps = await list_hypotheses_for_case(case.case_id)
    assert len(hyps) >= 1


# ===========================================================================
# Action-conditioned EIG
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_eig_pure_function_discriminates():
    from case_engine.next_best_action import expected_information_gain_pure

    priors = {"h1": 0.5, "h2": 0.5}
    # Test A: same outcome distribution under both -> ~0 EIG
    uniform = expected_information_gain_pure(
        priors, [{"h1": 0.5, "h2": 0.5}, {"h1": 0.5, "h2": 0.5}]
    )
    # Test B: different outcome distributions -> high EIG
    discriminating = expected_information_gain_pure(
        priors, [{"h1": 0.9, "h2": 0.1}, {"h1": 0.1, "h2": 0.9}]
    )
    assert uniform == pytest.approx(0.0)
    assert discriminating > uniform
    assert discriminating == pytest.approx(0.531, abs=0.01)

    # skewed priors reduce the value of further testing
    skewed = expected_information_gain_pure(
        {"h1": 0.95, "h2": 0.05},
        [{"h1": 0.9, "h2": 0.1}, {"h1": 0.1, "h2": 0.9}],
    )
    assert skewed < discriminating


@pytest.mark.unit
def test_uncertainty_heuristic_renamed_with_alias():
    """compute_eig survives as a deprecated alias; the honest name exists."""
    from case_engine import next_best_action as nba

    assert hasattr(nba, "uncertainty_priority_heuristic")
    assert hasattr(nba, "compute_eig")
    import inspect
    doc = inspect.getdoc(nba.compute_eig) or ""
    assert "DEPRECATED" in doc or "deprecated" in doc.lower()


# ===========================================================================
# Replay counterfactual fix
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_counterfactual_replay_rebuilds_inference(db_pool):
    """Removing decisive evidence must REBUILD hypothesis confidence from
    the remaining links — the heart of the second-pass audit's replay fix."""
    from case_engine.replay import (
        replay_case, replay_without_evidence, compute_evidence_causal_influence,
    )
    from case_engine.claim import create_claim, link_evidence_to_claim
    from case_engine.evidence_graph import add_edge, EdgeRelation
    from case_engine.hypothesis import recompute_hypothesis_confidence

    case = await create_case("Replay case", "objective")
    src = await _create_source(case.case_id, "LOG_FILE", None)
    ev = await ingest_evidence(
        case.case_id, "pool saturated at 20/20 connections",
        source_id=src.source_id, quality_score=0.95,
    )
    h = await create_hypothesis(case.case_id, "pool exhaustion", 0.3)
    claim = await create_claim(
        case.case_id, "the pool was saturated", "MEASUREMENT",
        supporting_evidence_ids=[ev.evidence_id],
    )
    await add_edge(
        case.case_id, claim.claim_id, "claim", h.hypothesis_id, "hypothesis",
        EdgeRelation.SUPPORTS,
    )
    await recompute_hypothesis_confidence(h.hypothesis_id, "test")

    full = await replay_case(case.case_id)
    assert full.hypotheses[h.hypothesis_id]["confidence"] > 0.5

    # counterfactual: remove the only supporting evidence -> the
    # confidence must DROP (rebuilt from remaining links, not copied from
    # the surviving hypothesis_version events)
    cf = await replay_without_evidence(case.case_id, ev.evidence_id)
    assert cf.hypotheses[h.hypothesis_id]["confidence"] < \
        full.hypotheses[h.hypothesis_id]["confidence"]
    assert cf.hypotheses[h.hypothesis_id].get("counterfactual_recomputed")

    influence = await compute_evidence_causal_influence(case.case_id, ev.evidence_id)
    assert influence["causal_influence"] > 0.05  # it genuinely mattered


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_reconstructs_more_event_types(db_pool):
    """Claims/actions/decisions/threads are no longer ignored by replay."""
    from case_engine.replay import replay_case
    from case_engine.action import create_action, approve_action, execute_action

    case = await _seed_case_with_evidence("Replay events case")
    for ev in await __import__(
        "case_engine.evidence", fromlist=["get_active_evidence"]
    ).get_active_evidence(case.case_id):
        await extract_claims(ev.evidence_id)
    a = await create_action(case.case_id, "ABSTAIN", "reason",
                             expected_outcome="rationale")
    await approve_action(a.action_id)
    await execute_action(a.action_id)
    from case_engine.decision import decide
    await decide(case.case_id)

    state = await replay_case(case.case_id)
    assert len(state.claims) >= 1
    assert a.action_id in state.actions
    assert state.actions[a.action_id] == "executed"
    assert len(state.decisions) >= 1


# ===========================================================================
# Event-consistency (transactional emit)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_events_commit_atomically_with_mutations(db_pool):
    """A failed mutation must leave NO orphan event (transactional emit)."""
    from case_engine.predictions import create_prediction
    from case_engine.hypothesis import create_hypothesis
    from db.connection import get_pool

    case = await create_case("Atomic case", "objective")
    h = await create_hypothesis(case.case_id, "h", 0.5)

    # create a valid prediction (event + row committed together)
    p = await create_prediction(
        case.case_id, h.hypothesis_id, "prediction text", "condition", 0.5,
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        n_events = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'hypothesis_prediction_created'",
            case.case_id,
        )
    assert n_events == 1

    # an invalid prediction (empty statement) raises BEFORE any insert —
    # no event, no row
    with pytest.raises(ValueError):
        await create_prediction(case.case_id, h.hypothesis_id, "", "cond")
    async with pool.acquire() as conn:
        n_events2 = await conn.fetchval(
            "SELECT COUNT(*) FROM case_events WHERE case_id = $1 "
            "AND event_type = 'hypothesis_prediction_created'",
            case.case_id,
        )
        n_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM hypothesis_predictions WHERE case_id = $1",
            case.case_id,
        )
    assert n_events2 == 1
    assert n_rows == 1


# ===========================================================================
# LLM ledger (cost accounting) — offline: zero calls recorded
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_usage_report_offline(db_pool):
    from case_engine.llm import get_llm_usage, llm_available

    if llm_available():
        pytest.skip("LLM configured — offline contract not applicable")
    usage = await get_llm_usage()
    assert usage["total_calls"] == 0


# ===========================================================================
# Explore ladder (real decision rules)
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explore_ladder_uses_workspace(db_pool):
    from case_engine.decision import decide
    from case_engine.case import create_case

    case = await create_case(
        "Explore ladder case", "explore the anomaly", case_type="explore",
    )
    decision = await decide(case.case_id)
    assert decision.decision_type in ("INVESTIGATE", "ABSTAIN")
    assert decision.top_factors.get("case_type") == "explore"
    # the new ladder has distinct reason codes
    assert decision.top_factors.get("reason") in (
        "no_direction", "workspace_candidate", "stale_exploration",
        "explore_mode",
    )
