"""case_engine/mechanism_lab.py — the Mechanism Laboratory / research ledger.

Owner:      Hari ICE "addition and improvements" pass. The document's own
            recommendation: "a structured catalogue of external mechanisms
            and our experiments with them ... a permanent record of what we
            discovered, what we borrowed, what worked, what failed, and why
            a particular external implementation was rejected."
            This module is BOTH the catalogue schema (mechanism_lab table,
            migration 020) and the SEED DATA — every donor mechanism the
            improvements document identified is preserved here in code form
            (origin domain, original problem, borrowed principle, inverted
            use, adaptations, resource profile, adoption decision), so the
            research survives the conversation that produced it — exactly
            the user's stated intent.
Deps:       db.connection (read/write), no engine deps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from db.connection import get_pool
from case_engine.exceptions import DatabaseUnavailableError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Seed catalogue — the donor mechanisms from the Hari ICE improvements
# document, preserved as structured records. seed_mechanism_lab() inserts
# them once (idempotent on mechanism_id).
# ---------------------------------------------------------------------------

SEED_MECHANISMS: list[dict] = [
    {
        "mechanism_id": "mech_ach_matrix",
        "name": "Analysis of Competing Hypotheses",
        "original_domain": "intelligence analysis (CIA Tradecraft Primer)",
        "original_problem": "premature closure on the first plausible explanation",
        "source_ref": "CIA Tradecraft Primer 2009, pp. 7-24",
        "borrowed_principle": (
            "evaluate evidence against ALL hypotheses in one matrix; prefer "
            "the least-contradicted hypothesis; seek discriminating evidence"
        ),
        "inverted_use": (
            "case_engine.causal.build_ach_matrix — claims x hypotheses "
            "matrix with least-contradicted / discriminating-claim "
            "diagnostics"
        ),
        "adaptations": [
            "ratings are deterministic token-overlap + explicit graph edges, not analyst judgment",
            "diagnostics name undiscriminating evidence (agreeable but useless)",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "implemented in case_engine/causal.py",
        "ice_module": "case_engine.causal",
    },
    {
        "mechanism_id": "mech_fsrs_resurfacing",
        "name": "FSRS spaced-repetition scheduling",
        "original_domain": "spaced repetition (Anki)",
        "original_problem": "scheduling flashcard review at the edge of forgetting",
        "source_ref": "open-spaced-repetition/fsrs4anki (FSRS-6)",
        "borrowed_principle": (
            "retrievability decays with time since last review; schedule "
            "resurfacing when retrievability drops, weighted by difficulty "
            "and stability"
        ),
        "inverted_use": (
            "resurface dormant questions, unexplained observations and "
            "neglected pursuits at useful times instead of letting them "
            "archive-rot"
        ),
        "adaptations": [
            "memory retrievability is NEVER belief confidence — truth does not decay",
            "age-based saturating urgency in endogenous.py source 7 as the deterministic first version",
            "workspace refractory decay (12h linear recovery) prevents resurfacing loops",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": (
            "deterministic age-based urgency shipped; full FSRS-6 parameter "
            "fitting deferred until review-history data exists"
        ),
        "ice_module": "case_engine.endogenous, case_engine.workspace",
    },
    {
        "mechanism_id": "mech_quality_diversity",
        "name": "Quality-Diversity archives (MAP-Elites)",
        "original_domain": "evolutionary optimization (pyribs)",
        "original_problem": "optimization collapses onto one peak, losing alternatives",
        "source_ref": "icaros-robotics/pyribs (MAP-Elites, novelty search)",
        "borrowed_principle": (
            "maintain the best candidate per behavior-descriptor cell "
            "instead of only the global best"
        ),
        "inverted_use": (
            "preserve genuinely different explanations (causal level x "
            "mechanism class cells) instead of five paraphrases of the "
            "leader"
        ),
        "adaptations": [
            "descriptors are (causal_level, mechanism_class, time_scale) instead of behavior features",
            "no evolutionary model — the archive is a plain grid; LLMs propose mutations later",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "implemented as quality_diversity_select in case_engine/induction.py",
        "ice_module": "case_engine.induction",
    },
    {
        "mechanism_id": "mech_hypothesis_tournament",
        "name": "Generate-critique-evolve hypothesis tournaments",
        "original_domain": "scientific discovery agents (Co-Scientist, Robin)",
        "original_problem": "one LLM returning one answer conflates generation with evaluation",
        "source_ref": "Nature 2026 Co-Scientist; Robin (pairwise round-robin, <=25 candidates)",
        "borrowed_principle": (
            "separate generation, critique and ranking; compare candidates "
            "PAIRWISE; revise using actual observations"
        ),
        "inverted_use": (
            "deterministic pipeline of typed steps (induce -> diversify -> "
            "critique -> tournament) instead of autonomous agents — the "
            "Robin lesson that a deterministic pipeline can beat an "
            "elaborate agentic architecture"
        ),
        "adaptations": [
            "deterministic offline fallback so the loop runs without any LLM",
            "critique and tournament are cost-capped (10 pairs)",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": "optional"},
        "decision": "adapt",
        "decision_note": "implemented in case_engine/induction.py",
        "ice_module": "case_engine.induction",
    },
    {
        "mechanism_id": "mech_action_conditioned_eig",
        "name": "Expected information gain over outcome models",
        "original_domain": "experimental design / value of information",
        "original_problem": "choosing experiments that maximally reduce uncertainty",
        "source_ref": "decision theory (VOI); EIG(a) = H(Θ) − Σ P(o|a)H(Θ|o,a)",
        "borrowed_principle": (
            "score a TEST by the expected posterior entropy reduction it "
            "produces, integrating over predicted outcomes"
        ),
        "inverted_use": (
            "rank investigation actions by true action-conditioned EIG "
            "instead of the standing uncertainty of the hypothesis set"
        ),
        "adaptations": [
            "outcome models: attached (planner) > LLM > deterministic default",
            "old heuristic retained under its honest name uncertainty_priority_heuristic",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": "optional"},
        "decision": "adapt",
        "decision_note": "expected_information_gain_pure + expected_information_gain in next_best_action.py",
        "ice_module": "case_engine.next_best_action",
    },
    {
        "mechanism_id": "mech_attention_portfolio",
        "name": "Portfolio allocation of attention",
        "original_domain": "personal finance / portfolio management",
        "original_problem": "capital concentrating in the best recent performer",
        "source_ref": "diversification, risk budgeting, opportunity cost",
        "borrowed_principle": (
            "allocate across categories with quotas; recent performance "
            "cannot consume the whole budget; option value of unexplored "
            "questions"
        ),
        "inverted_use": (
            "workspace selection under immediate/maintenance/development/"
            "exploration/speculation quotas so short-term relevance cannot "
            "monopolize cognition"
        ),
        "adaptations": [
            "default shares are a starting hypothesis, not tuned values",
            "speculation keeps 5% even against urgent work (high-impact low-probability preservation)",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "PORTFOLIO_DEFAULTS in case_engine/workspace.py",
        "ice_module": "case_engine.workspace",
    },
    {
        "mechanism_id": "mech_forecast_scoring",
        "name": "Forecast records with resolution conditions",
        "original_domain": "forecasting platforms (Metaculus)",
        "original_problem": "predictions without accountability",
        "source_ref": "Metaculus scoring (log scores, resolution conditions)",
        "borrowed_principle": (
            "every prediction carries a resolution condition and probability; "
            "score when the world answers"
        ),
        "inverted_use": (
            "hypothesis predictions as first-class records; calibration "
            "report kept strictly separate from confidence heuristics"
        ),
        "adaptations": [
            "Brier score (1-brier stored, higher-is-better convention)",
            "confidence is NOT a probability — prediction probability is its own field",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "case_engine/predictions.py (migration 018)",
        "ice_module": "case_engine.predictions",
    },
    {
        "mechanism_id": "mech_process_mining",
        "name": "Directly-follows process discovery",
        "original_domain": "process mining (PM4Py)",
        "original_problem": "understanding actual workflows from event logs",
        "source_ref": "pm4py discover_dfg / conformance checking",
        "borrowed_principle": (
            "discover the directly-follows graph from the event log; compare "
            "actual flow against the intended model"
        ),
        "inverted_use": (
            "introspect ICE's own reasoning behavior: stall stages, "
            "proposed-never-executed actions, loops, unresolved endings "
            "per case type"
        ),
        "adaptations": [
            "pure SQL+Python DFG instead of the PM4Py dependency",
            "loop detection + execution-rate diagnostics on top",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "case_engine/introspection.py",
        "ice_module": "case_engine.introspection",
    },
    {
        "mechanism_id": "mech_failure_localization",
        "name": "Trajectory failure localization",
        "original_domain": "agent trajectory diagnosis (AgentRx, EMNLP 2026)",
        "original_problem": "grading only the final answer hides WHERE the agent failed",
        "source_ref": "microsoft/AgentRx (invariants -> constraint check -> failure class)",
        "borrowed_principle": (
            "check invariants over the trajectory; report the FIRST "
            "meaningful failure; classify its type"
        ),
        "inverted_use": (
            "rule-based invariants over case_events (execution without "
            "approval, belief without observation, contradictions without "
            "follow-up, activity without progress)"
        ),
        "adaptations": [
            "no LLM judge — deterministic rules only; Azure-oriented client replaced by SQL",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "case_engine/trajectory.py",
        "ice_module": "case_engine.trajectory",
    },
    {
        "mechanism_id": "mech_memory_operation_eval",
        "name": "Encoding/retrieval/reasoning separation",
        "original_domain": "agent memory evaluation (EvalMem, 2026)",
        "original_problem": "'it forgot' conflates three different failures",
        "source_ref": "EvalMem / MemWiki (search-friendly layer over memory)",
        "borrowed_principle": (
            "evaluate encoding, retrieval and reasoning as separate "
            "operations before building new memory machinery"
        ),
        "inverted_use": (
            "memory_diagnostic_report classifies each expected fact's "
            "failure mode and routes the fix (extraction vs search vs "
            "reasoning)"
        ),
        "adaptations": ["deterministic token-coverage matching, no LLM judge"],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "case_engine/diagnostics.py",
        "ice_module": "case_engine.diagnostics",
    },
    {
        "mechanism_id": "mech_chain_of_custody",
        "name": "Epistemic chain of custody",
        "original_domain": "digital forensics",
        "original_problem": "establishing what was changed, when, and by whom",
        "source_ref": "forensic evidence handling; SARIF fingerprints",
        "borrowed_principle": (
            "every derived belief traceable to sources and intermediate "
            "reasoning; preserve incorrect interpretations, don't erase them"
        ),
        "inverted_use": (
            "event-sourced case history + derived-evidence lineage "
            "(metadata.derived_from) + versioned hypotheses: you can always "
            "answer 'why did it once believe this?'"
        ),
        "adaptations": [
            "derived evidence (compare executor) is MODEL_INFERENCE with weight 0.0 — it records reasoning without confirming it",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "reuse",
        "decision_note": "existing event/provenance plane extended, not replaced",
        "ice_module": "case_engine (provenance, events, versions)",
    },
    {
        "mechanism_id": "mech_contextual_bandits",
        "name": "Learnable attention policies",
        "original_domain": "contextual bandits (Vowpal Wabbit)",
        "original_problem": "fixed policies cannot adapt to situations",
        "source_ref": "VW contextual bandits (IPS, doubly robust evaluation)",
        "borrowed_principle": (
            "learn which candidates deserve attention in which context, "
            "with logged propensities for offline evaluation"
        ),
        "inverted_use": (
            "eventual learned workspace selection; deterministic "
            "attention-with-portfolio first, bandit later"
        ),
        "adaptations": [
            "deferred until reliable logs of (context, action, propensity, reward) exist — the document's own caveat",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "defer",
        "decision_note": (
            "workspace_items already logs context+score+selection; the "
            "learned policy can be evaluated offline once enough cycles "
            "accumulate"
        ),
        "ice_module": "case_engine.workspace (future)",
    },
    {
        "mechanism_id": "mech_socratic_recommender",
        "name": "Preference / metapreference separation",
        "original_domain": "conversational recommenders (Socratic RS, 2026)",
        "original_problem": "recommenders treat observed behavior as endorsed preference",
        "source_ref": "Springer 2026: first-order vs second-order desires",
        "borrowed_principle": (
            "distinguish observed behavior, expressed desire, endorsed "
            "preference and metapreference; recommendations as reflective "
            "questions, not echo chambers"
        ),
        "inverted_use": (
            "workspace 'observed vs pursued' candidates; plan "
            "reconciliation keeps 'what the user said they wanted' "
            "separate from 'what the code now does'; never silently "
            "collapse them"
        ),
        "adaptations": [
            "plan reconcile defaults to 'unresolved' rather than guessing intent",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": "separation encoded in plan.py records + workspace candidate types",
        "ice_module": "case_engine.plan, case_engine.workspace",
    },
    {
        "mechanism_id": "mech_goap_planning",
        "name": "Goal-oriented action planning",
        "original_domain": "game AI / robotics (GOAP, STRIPS)",
        "original_problem": "action labels without preconditions and effects",
        "source_ref": "GOAP; pyperplan (STRIPS); BehaviorTree.CPP for interruption",
        "borrowed_principle": (
            "actions as (preconditions, effects, cost); plans as sequences "
            "toward goal states; reactive re-planning when conditions change"
        ),
        "inverted_use": (
            "action contracts (outcome semantics + discriminating-test "
            "planner) as the first step; full precondition/effect planning "
            "when tests exist to prove it helps"
        ),
        "adaptations": [
            "outcome vocabulary + expected_outcome field shipped now",
            "goal system (case_engine/goals.py) shipped: typed intent "
            "graph, clarity levels, provenance, supersede chains — the "
            "goal-state half of GOAP",
            "full precondition/effect planning still deferred — the "
            "document's warning against premature machinery",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "adapt",
        "decision_note": (
            "goal-state representation adapted and shipped (goals.py: "
            "typed intent graph, clarity levels, provenance, supersede "
            "chains); action precondition/effect chains remain deferred"
        ),
        "ice_module": "case_engine.goals + case_engine.action (partial)",
    },
    {
        "mechanism_id": "mech_activity_watchers",
        "name": "Independent event watchers",
        "original_domain": "personal activity tracking (ActivityWatch)",
        "original_problem": "understanding where attention actually goes",
        "source_ref": "ActivityWatch watcher architecture, local SQLite, heartbeats",
        "borrowed_principle": (
            "independent watchers emit typed events to a local server; "
            "custom watchers via API; data stays local"
        ),
        "inverted_use": (
            "future personal-observability sensors feeding workspace "
            "candidates (behavioral observations, never established facts "
            "about thoughts)"
        ),
        "adaptations": [
            "deferred: requires deployment-level integration beyond the engine",
        ],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "revisit",
        "decision_note": "candidate sources are pluggable in workspace.py already",
        "ice_module": "(external integration, future)",
    },
    {
        "mechanism_id": "mech_research_feeds",
        "name": "Background research awareness",
        "original_domain": "RSS readers / web monitors (Miniflux, Huginn, Karakeep)",
        "original_problem": "missing developments relevant to open questions",
        "source_ref": "Miniflux API; Huginn agents; Karakeep capture",
        "borrowed_principle": (
            "low-cost filter decides whether an update deserves deeper "
            "analysis; capture is frictionless, analysis is deliberate"
        ),
        "inverted_use": (
            "future environmental observation layer feeding evidence "
            "ingestion; only then requesting LLM analysis"
        ),
        "adaptations": ["deferred: external services, not engine modules"],
        "resource_profile": {"gpu_required": False, "llm_required": False},
        "decision": "revisit",
        "decision_note": "evidence ingestion + source families already support it",
        "ice_module": "(external integration, future)",
    },
]


async def seed_mechanism_lab() -> int:
    """Insert the seed catalogue once (idempotent). Returns inserted count."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("Cannot seed mechanism lab: no DB pool")
    inserted = 0
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        for m in SEED_MECHANISMS:
            exists = await conn.fetchval(
                "SELECT 1 FROM mechanism_lab WHERE mechanism_id = $1",
                m["mechanism_id"],
            )
            if exists:
                continue
            import json as _json

            await conn.execute(
                """
                INSERT INTO mechanism_lab (
                    mechanism_id, name, original_domain, original_problem,
                    source_ref, borrowed_principle, inverted_use,
                    adaptations, experiment_status, resource_profile,
                    decision, decision_note, ice_module, created_at, updated_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8::text[], 'proposed',
                    $9::jsonb, $10, $11, $12, $13, $13
                )
                """,
                m["mechanism_id"], m["name"], m["original_domain"],
                m["original_problem"], m["source_ref"],
                m["borrowed_principle"], m["inverted_use"],
                m["adaptations"], _json.dumps(m["resource_profile"]),
                m["decision"], m["decision_note"], m["ice_module"], now,
            )
            inserted += 1
    return inserted


async def list_mechanisms(decision: Optional[str] = None) -> list[dict]:
    """List catalogue entries (optionally by decision: reuse/adapt/reject/defer/revisit)."""
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    if decision:
        rows = await _fetch(pool, decision)
    else:
        rows = await _fetch(pool, None)
    return rows


async def _fetch(pool, decision) -> list[dict]:
    async with pool.acquire() as conn:
        if decision:
            raw = await conn.fetch(
                "SELECT * FROM mechanism_lab WHERE decision = $1 "
                "ORDER BY name", decision,
            )
        else:
            raw = await conn.fetch(
                "SELECT * FROM mechanism_lab ORDER BY name"
            )
    return [
        {
            "mechanism_id": r["mechanism_id"],
            "name": r["name"],
            "original_domain": r["original_domain"],
            "original_problem": r["original_problem"],
            "source_ref": r["source_ref"],
            "borrowed_principle": r["borrowed_principle"],
            "inverted_use": r["inverted_use"],
            "adaptations": list(r["adaptations"] or []),
            "experiment_status": r["experiment_status"],
            "evaluation": r["evaluation"],
            "resource_profile": r["resource_profile"],
            "decision": r["decision"],
            "decision_note": r["decision_note"],
            "ice_module": r["ice_module"],
        }
        for r in raw
    ]


async def update_mechanism_decision(
    mechanism_id: str,
    decision: str,
    decision_note: str = "",
    experiment_status: Optional[str] = None,
    evaluation: Optional[str] = None,
) -> None:
    """Record an adopt/adapt/reject/defer/revisit decision with its reason."""
    if decision not in ("pending", "reuse", "adapt", "reject", "defer", "revisit"):
        raise ValueError(f"invalid decision: {decision}")
    pool = await get_pool()
    if pool is None:
        raise DatabaseUnavailableError("no DB pool")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE mechanism_lab
            SET decision = $1, decision_note = $2,
                experiment_status = COALESCE($3, experiment_status),
                evaluation = COALESCE($4, evaluation),
                updated_at = $5
            WHERE mechanism_id = $6
            """,
            decision, decision_note, experiment_status, evaluation,
            datetime.now(timezone.utc), mechanism_id,
        )
