"""scripts/verify_part3.py — End-to-end verification of Part 3 modules.

Owner:      ICE revamp, spec Part 3 §11.
Usage:      python scripts/verify_part3.py
            (requires Postgres + pgvector and LLM/embedding keys via .env;
            run in the Docker environment — there is no DB in the sandbox
            build environment.)

IMPORTANT: the ENABLE_FALSIFICATION_LOOP / ENABLE_NEXT_BEST_EVIDENCE env
flags are set with setdefault BEFORE any case_engine import — those flags
are bound to module constants at import time (case_engine.action /
case_engine.next_best_action), so setting them after import would be a
no-op (Part 3 §0 rule 13).

Exercises, in order (15 printed steps):
    [1]  create_case + 3 evidence ingests (HUMAN source)
    [2]  extract_claims from E1 (>= 1 claim)
    [3]  two opposing claims + 3 hypotheses (0.4 / 0.35 / 0.25)
    [4]  evidence_graph add_edge: SUPPORTS + CONTRADICTS -> h2
    [5]  detect_contradiction (not None, severity >= 0.7)
    [6]  create_gap discriminating [h1, h3] + compute_gap_priority
    [7]  InvestigationThreadRegistry: two threads + reinforce_from_winner
         (matched == t1)
    [8]  get_hypothesis_graph (>= 1 supporting, >= 1 contradicting claim)
    [9]  compute_evidence_trace_coverage in [0,1] +
         compute_independence_score == 1.0
    [10] generate_actions
    [11] select_next_best_action (None allowed — printed either way)
    [12] decide (ACT / INVESTIGATE / ABSTAIN)
    [13] replay_case (event_count > 0)
    [14] compute_evidence_causal_influence for E1
    [15] get_case_summary

Acceptance: the script exits with status 0 and prints
"Part 3 verification PASSED".
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

# Rule 13 flags MUST be set before case_engine imports (module-level
# constants are bound at import time).
os.environ.setdefault("ENABLE_FALSIFICATION_LOOP", "false")
os.environ.setdefault("ENABLE_NEXT_BEST_EVIDENCE", "true")

from case_engine.case import create_case, get_case_summary
from case_engine.evidence import _create_source, ingest_evidence
from case_engine.claim import extract_claims, create_claim
from case_engine.hypothesis import create_hypothesis
from case_engine.contradiction import detect_contradiction
from case_engine.information_gap import create_gap, compute_gap_priority
from case_engine.investigation_thread import (
    InvestigationThreadRegistry,
    reinforce_from_winner,
)
from case_engine.evidence_graph import (
    add_edge,
    EdgeRelation,
    get_hypothesis_graph,
)
from case_engine.provenance import (
    compute_evidence_trace_coverage,
    compute_independence_score,
)
from case_engine.action import generate_actions
from case_engine.next_best_action import select_next_best_action
from case_engine.decision import decide
from case_engine.replay import replay_case, compute_evidence_causal_influence


async def main() -> None:
    # [1] Case + three evidence items -----------------------------------
    case = await create_case("Incident", "Identify root cause")

    src = await _create_source(case.case_id, "HUMAN", None)
    ev1 = await ingest_evidence(
        case.case_id,
        "After rollback, p95 latency returned to 240ms.",
        src.source_id,
    )
    ev2 = await ingest_evidence(
        case.case_id,
        "Cache hit rate dropped from 95% to 40% during the incident.",
        src.source_id,
    )
    ev3 = await ingest_evidence(
        case.case_id,
        "Cache hit rate remained at 95% throughout the incident.",
        src.source_id,
    )
    print(f"[1] Evidence: {ev1.evidence_id}, {ev2.evidence_id}, {ev3.evidence_id}")

    # [2] Claims from E1 ---------------------------------------------------
    claims = await extract_claims(ev1.evidence_id)
    assert len(claims) >= 1
    print(f"[2] Claims from E1: {len(claims)}")

    # Two explicitly-created opposing claims (MEASUREMENT, evidence-backed)
    c_cache_drop = await create_claim(
        case.case_id,
        "Cache hit rate dropped",
        "MEASUREMENT",
        supporting_evidence_ids=[ev2.evidence_id],
    )
    c_cache_unchanged = await create_claim(
        case.case_id,
        "Cache hit rate unchanged",
        "MEASUREMENT",
        supporting_evidence_ids=[ev3.evidence_id],
    )

    # [3] Hypotheses ---------------------------------------------------------
    h1 = await create_hypothesis(case.case_id, "Connection pool regression", 0.4)
    h2 = await create_hypothesis(case.case_id, "Cache invalidation bug", 0.35)
    h3 = await create_hypothesis(case.case_id, "Regional infrastructure", 0.25)
    print(f"[3] Hypotheses: {h1.hypothesis_id}, {h2.hypothesis_id}, {h3.hypothesis_id}")

    # [4] Link claims to h2 via the evidence graph ---------------------------
    await add_edge(
        case.case_id,
        c_cache_drop.claim_id, "claim",
        h2.hypothesis_id, "hypothesis",
        EdgeRelation.SUPPORTS,
    )
    await add_edge(
        case.case_id,
        c_cache_unchanged.claim_id, "claim",
        h2.hypothesis_id, "hypothesis",
        EdgeRelation.CONTRADICTS,
    )
    print("[4] Graph edges added")

    # [5] Contradiction detection ---------------------------------------------
    contra = await detect_contradiction(
        c_cache_drop.claim_id, c_cache_unchanged.claim_id
    )
    assert contra is not None, "Expected contradiction to be detected"
    assert contra.severity >= 0.7
    print(f"[5] Contradiction: {contra.contradiction_id} severity={contra.severity:.2f}")

    # [6] Information gap + priority --------------------------------------------
    gap = await create_gap(
        case.case_id,
        "Connection reuse metrics before/after deploy",
        "Discriminates H1 from H3",
        [h1.hypothesis_id, h3.hypothesis_id],
    )
    priority = await compute_gap_priority(gap.gap_id)
    print(f"[6] Gap: {gap.gap_id} priority={priority:.2f}")

    # [7] Investigation threads ---------------------------------------------------
    registry = InvestigationThreadRegistry(case.case_id)
    t1 = await registry.create_thread(
        "Investigate cache invalidation timing", "contradiction",
        decision_impact=0.8, uncertainty=0.7,
    )
    t2 = await registry.create_thread(
        "Investigate regional latency variance", "gap",
        decision_impact=0.6, uncertainty=0.5,
    )
    matched = await reinforce_from_winner(
        registry, "Investigate cache invalidation timing", current_turn=1,
    )
    assert matched == t1.thread_id
    print(f"[7] Thread reinforced: {matched} (of {t1.thread_id}, {t2.thread_id})")

    # [8] Hypothesis graph -----------------------------------------------------
    graph = await get_hypothesis_graph(h2.hypothesis_id)
    assert len(graph["supporting_claims"]) >= 1
    assert len(graph["contradicting_claims"]) >= 1
    print(
        f"[8] Graph: {len(graph['supporting_claims'])} support, "
        f"{len(graph['contradicting_claims'])} contra"
    )

    # [9] Provenance ---------------------------------------------------------------
    etc = await compute_evidence_trace_coverage(case.case_id)
    assert 0.0 <= etc <= 1.0
    indep = await compute_independence_score(src.source_id, case.case_id)
    assert indep == 1.0
    print(f"[9] ETC={etc:.2f} independence={indep}")

    # [10] Actions ---------------------------------------------------------------------
    actions = await generate_actions(case.case_id)
    print(f"[10] Generated {len(actions)} actions")

    # [11] Next-best action (None is acceptable) --------------------------------------
    nba = await select_next_best_action(case.case_id)
    if nba is not None:
        action, prio = nba
        print(f"[11] Best action: {action.action_type} priority={prio:.2f}")
    else:
        print("[11] No actions to select")

    # [12] Decision ---------------------------------------------------------------------
    decision = await decide(case.case_id)
    assert decision.decision_type in ("ACT", "INVESTIGATE", "ABSTAIN")
    print(f"[12] Decision: {decision.decision_type} ({decision.confidence:.2f})")

    # [13] Replay ------------------------------------------------------------------------
    state = await replay_case(case.case_id)
    assert state.event_count > 0
    print(
        f"[13] Replay: {state.event_count} events, "
        f"leading_conf={state.leading_hypothesis_confidence:.2f}"
    )

    # [14] Counterfactual causal influence of E1 ------------------------------------------
    influence = await compute_evidence_causal_influence(case.case_id, ev1.evidence_id)
    print(
        f"[14] Causal influence of E1: {influence['causal_influence']:.3f} "
        f"({influence['interpretation']})"
    )

    # [15] Summary --------------------------------------------------------------------------
    summary = await get_case_summary(case.case_id)
    print(f"[15] Summary: {summary['counts']}")

    print("\n✅ Part 3 verification PASSED")


if __name__ == "__main__":
    asyncio.run(main())