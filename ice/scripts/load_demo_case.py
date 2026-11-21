"""scripts/load_demo_case.py — populate three demo cases for the UI.

Creates (one per case_type — Part 2 §6.1, Feature 2E):
1. A debug case (checkout latency incident) — classic demo of what ICE
   already does well: the confidence ladder drives resolution, so
   case_type="debug" with an explicit required_confidence=0.85 (the
   engine's debug default).
2. A research case (cognitive architecture survey) — research-mode pitch:
   gaps and synthesis over imported reading material, so
   case_type="research" with required_confidence=0.0 (the engine's
   research/explore default — their decide() ladders use no confidence
   threshold).
3. An explore case with external LLM analysis ingested —
   LLM-collaboration pitch: the user asks Claude/GPT outside ICE and files
   the analyses as MODEL_INFERENCE evidence via ingest_external_analysis
   (deduplicated on (llm, prompt) hash), with hypotheses whose origin
   attributes the external LLM's agency (origin="external_llm" +
   origin_detail). case_type="explore", required_confidence=0.0.

Note (ICE fixes doc Part 3 §0.7): this is the SINGLE canonical demo
script. Oct 23's `scripts/seed_demo.py` was never available to recover
(the Oct 23 directory was not provided to this environment), so this
three-case rewrite of Oct 22's `load_demo_case.py` supersedes both.

Usage:  python scripts/load_demo_case.py
        (requires Postgres + pgvector and LLM keys via .env; run in the
        Docker environment.)
Output: prints the three case_ids, each case's summary counts, and a
        pointer to open the UI.
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from db.connection import init_db, close_db
from case_engine.case import create_case, get_case_summary
from case_engine.evidence import (
    _create_source, ingest_evidence, ingest_external_analysis,
)
from case_engine.claim import extract_claims, create_claim
from case_engine.hypothesis import create_hypothesis
from case_engine.evidence_graph import add_edge, EdgeRelation
from case_engine.information_gap import create_gap
from case_engine.investigation_thread import InvestigationThreadRegistry
from case_engine.cycle import run_investigation_cycle  # canonical module (renamed from case_engine.investigation, merge spec Part 3 §0.6)


async def load_debug_case() -> str:
    """Checkout latency demo — what ICE already does well.

    case_type="debug", required_confidence=0.85 (the debug default,
    stated explicitly): the decide() confidence ladder can drive the case
    to ACT once a hypothesis clears 0.85.
    """
    case = await create_case(
        title="Checkout latency regression (demo)",
        objective="Identify the root cause of the p95 latency increase after deploy v42",
        required_confidence=0.85,
        case_type="debug",
    )
    src = await _create_source(case.case_id, "HUMAN", None)

    evidence_texts = [
        "p95 checkout latency rose from 240ms to 890ms after the 14:00 "
        "deployment of v42; the 16:30 rollback restored 240ms.",
        "Deployment v42 changed the connection pool config: "
        "max_connections went from 100 to 20, and cache TTL from 300s "
        "to 60s.",
        "I saw connection pool exhaustion errors. Pool saturated within "
        "minutes of deploy, cache hit rate stayed near 95%.",
    ]
    ev_ids = []
    for text in evidence_texts:
        ev = await ingest_evidence(case.case_id, text, src.source_id)
        ev_ids.append(ev.evidence_id)
        try:
            await extract_claims(ev.evidence_id)
        except Exception as exc:
            print(f"  claim extraction skipped for {ev.evidence_id}: {exc}")

    h1 = await create_hypothesis(
        case.case_id, "Connection pool regression", 0.4, origin="user",
    )
    h2 = await create_hypothesis(
        case.case_id, "Cache invalidation bug", 0.3, origin="user",
    )
    h3 = await create_hypothesis(
        case.case_id, "Regional infrastructure", 0.3, origin="user",
    )

    # Two directly-authored claims anchor the evidence graph even when LLM
    # claim extraction is unavailable (keys missing / circuit breaker open).
    c1 = await create_claim(
        case.case_id, "Connection pool saturated after deploy", "OBSERVATION",
        supporting_evidence_ids=[ev_ids[1]],
    )
    c2 = await create_claim(
        case.case_id, "Cache hit rate stayed at 95%", "MEASUREMENT",
        supporting_evidence_ids=[ev_ids[2]],
    )
    await add_edge(
        case.case_id, c1.claim_id, "claim", h1.hypothesis_id, "hypothesis",
        EdgeRelation.SUPPORTS,
    )
    await add_edge(
        case.case_id, c2.claim_id, "claim", h2.hypothesis_id, "hypothesis",
        EdgeRelation.CONTRADICTS,
    )

    await create_gap(
        case.case_id,
        "Connection pool utilization before/after deploy",
        "Discriminates pool regression from regional infrastructure",
        [h1.hypothesis_id, h3.hypothesis_id],
    )

    registry = InvestigationThreadRegistry(case.case_id)
    await registry.create_thread(
        "Investigate pool saturation timing", "gap",
        decision_impact=0.8, uncertainty=0.6,
    )

    await run_investigation_cycle(case.case_id)

    summary = await get_case_summary(case.case_id)
    print(f"[debug] case_id: {case.case_id}")
    print(f"[debug] summary: {summary['counts']}")
    return case.case_id


async def load_research_case() -> str:
    """Cognitive architecture survey — research-mode pitch.

    case_type="research", required_confidence=0.0 (the research default):
    decide() runs the 4-rule research ladder (no-hypotheses →
    active-contradictions → high-priority-gaps → ready-to-synthesize)
    instead of the debug confidence threshold.
    """
    case = await create_case(
        title="Cognitive architecture survey (demo)",
        objective="Synthesize the mechanisms most useful for an investigation-continuity engine",
        required_confidence=0.0,
        case_type="research",
    )
    src = await _create_source(case.case_id, "DOCUMENT", "survey_material.md")

    papers = [
        "Active Inference (Friston): expected free energy guides action "
        "selection. EIG(a) = H(beliefs) - E[H(beliefs | outcome)]. Planning "
        "heuristic, not calibrated Bayesian.",
        "Global Workspace Theory (Baars): consciousness as a broadcast "
        "bottleneck. Candidates compete for the workspace; winners "
        "broadcast to subsystems.",
        "ACT-R (Anderson): activation-based memory. B_i = log(sum t_j^-d). "
        "Recency and frequency both matter. ICE uses this for retrieval "
        "scoring.",
        "GraphRAG (Microsoft): entity-relationship graphs improve multi-hop "
        "reasoning. Retrieval over a graph outperforms retrieval over flat "
        "documents for questions requiring connection across sources.",
        "Reflexion (Shinn et al): verbal reinforcement learning. Agents "
        "that reflect on failures and store lessons outperform those "
        "that don't.",
        "NeuRRAM: alternative memory architecture. Contradicts the von "
        "Neumann assumption — computation and memory co-located. Not "
        "directly useful for ICE but a tension worth noting.",
    ]

    for i, text in enumerate(papers):
        ev = await ingest_evidence(
            case.case_id, text, src.source_id,
            metadata={"paper_index": i, "added_by": "demo_script"},
        )
        try:
            await extract_claims(ev.evidence_id)
        except Exception as exc:
            print(f"  claim extraction skipped for {ev.evidence_id}: {exc}")

    h1 = await create_hypothesis(
        case.case_id,
        "Attention-based competition is essential for investigation state management",
        0.5, origin="user",
    )
    h2 = await create_hypothesis(
        case.case_id,
        "ACT-R activation scoring can be applied to evidence retrieval",
        0.5, origin="user",
    )

    await create_gap(
        case.case_id,
        "Primary source for GraphRAG's multi-hop reasoning claim",
        "The current material cites a secondary source; need the original",
        [h1.hypothesis_id],
        priority=0.7,
    )
    await create_gap(
        case.case_id,
        "Whether NeuRRAM's memory architecture is relevant to ICE's evidence model",
        "Note the tension with von Neumann; decide if it applies",
        [h2.hypothesis_id],
        priority=0.4,
    )

    await run_investigation_cycle(case.case_id)

    summary = await get_case_summary(case.case_id)
    print(f"[research] case_id: {case.case_id}")
    print(f"[research] summary: {summary['counts']}")
    return case.case_id


async def load_explore_case() -> str:
    """LLM-collaboration workflow — user asks Claude, ingests the analysis.

    case_type="explore", required_confidence=0.0 (the explore default):
    decide() is the documented explore stub (always INVESTIGATE — doc
    Part 2 §12 shortcut #6). The point of this case is the ingestion
    pattern: two ingest_external_analysis calls (claude-3.5-sonnet +
    gpt-4o) become MODEL_INFERENCE evidence with source_llm set, and the
    hypotheses carry origin="external_llm" so the UI can attribute them.
    """
    case = await create_case(
        title="Is my ICE project ready to ship? (demo)",
        objective="Decide whether the investigation engine has a defensible pitch",
        required_confidence=0.0,
        case_type="explore",
    )

    src = await _create_source(case.case_id, "HUMAN", None)
    await ingest_evidence(
        case.case_id,
        "I've built a state-tracking engine for multi-session investigations. "
        "It has 23 modules, 13 tables, event sourcing, versioned hypotheses. "
        "I don't know if it's ready to present.",
        src.source_id,
    )

    await ingest_external_analysis(
        case_id=case.case_id,
        llm_name="claude-3.5-sonnet",
        prompt_used="I've built a state-tracking engine. How should I pitch it?",
        analysis_text=(
            "The state-tracking engine is defensible because persistent "
            "hypothesis versioning and evidence provenance solve a real "
            "problem — the reasoning that dies every evening. The pitch "
            "should emphasize 'state keeper for investigations', not "
            "'cognitive architecture'. The gaps are: no test suite, no "
            "benchmark numbers. Those are the blockers, not the design."
        ),
        conversation_context=(
            "User is a developer who directed an AI to build this project "
            "and needs to present it. They understand the design but need "
            "to verify it runs."
        ),
    )

    await ingest_external_analysis(
        case_id=case.case_id,
        llm_name="gpt-4o",
        prompt_used="What would you say is the biggest weakness of this project?",
        analysis_text=(
            "The biggest weakness is that it hasn't been run end-to-end. "
            "Every design decision is defensible, but until docker-compose "
            "up and the verify scripts actually pass, 'the code exists' is "
            "not 'the code works'. The second weakness is that the "
            "claim-hypothesis support links are manual, which limits the "
            "confidence loop's autonomy."
        ),
    )

    h1 = await create_hypothesis(
        case.case_id,
        "The engine is presentation-ready pending runtime verification",
        0.5,
        origin="external_llm",
        origin_detail="aggregated from claude-3.5-sonnet and gpt-4o",
    )
    h2 = await create_hypothesis(
        case.case_id,
        "The manual claim-hypothesis link is the primary functional limitation",
        0.5,
        origin="external_llm",
        origin_detail="gpt-4o",
    )

    await run_investigation_cycle(case.case_id)

    summary = await get_case_summary(case.case_id)
    print(f"[explore] case_id: {case.case_id}")
    print(f"[explore] summary: {summary['counts']}")
    return case.case_id


async def main() -> None:
    await init_db()
    try:
        print("Loading demo cases...\n")
        debug_id = await load_debug_case()
        print()
        research_id = await load_research_case()
        print()
        explore_id = await load_explore_case()
        print()
        print("=" * 60)
        print(f"Debug case: {debug_id}")
        print(f"Research case: {research_id}")
        print(f"Explore case: {explore_id}")
        print("=" * 60)
        print("Open http://localhost:8501 and select any case from the sidebar.")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())